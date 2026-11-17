# server_es.py — Dialogflow ES MCP Server with PostgreSQL
#
# South African Food Ordering Chatbot MCP Server.
# Integrates Dialogflow ES natural language understanding with a PostgreSQL
# backend for menu lookup, order placement, order tracking, and conversation logging.

import functools
import json
import logging
import os
import re
import threading
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Any, ParamSpec, TypeVar

import anyio
import psycopg
from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import dialogflow_v2 as dialogflow
from google.protobuf.json_format import MessageToDict
from mcp.server.fastmcp import FastMCP

from dialogflow_mcp.database import (
    execute,
    fetch_all,
    fetch_one,
    transaction,
)
from dialogflow_mcp.gcp_auth import client_kwargs

# Never print to stdout: the stdio MCP transport uses it for protocol messages.
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────
PROJECT = os.environ.get("GCP_PROJECT", "")
LOCATION = os.environ.get("DF_LOCATION", "global")
ENVIRONMENT = os.environ.get("DF_ENVIRONMENT", "")

# Admin tools (update_order_status, get_recent_orders, get_conversation_logs)
# are only registered when MCP_ADMIN_TOOLS is truthy. Defaults to "on".
ADMIN_TOOLS_ENABLED = os.environ.get("MCP_ADMIN_TOOLS", "on").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

MAX_QTY_PER_ITEM = 50
MAX_LIST_LIMIT = 100
MAX_MATCH_CANDIDATES = 10

# complete_order bounds: each distinct item costs a lookup inside the order
# transaction, so cap the work one call can do (the menu has 9 items).
MAX_ORDER_ENTRIES = 50
MAX_ORDER_LINES = 20

# orders.order_id is a Postgres INTEGER (SERIAL).
MAX_ORDER_ID = 2_147_483_647

# Dialogflow ES session ids: at most 36 characters; restrict to a safe charset
# because the id goes into the session resource path and conversation_logs.
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,36}$")

# Dialogflow ES text query limit, and a BCP-47-style language tag (e.g. "en", "en-US").
MAX_QUERY_TEXT_LEN = 256
LANGUAGE_CODE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")

# Column widths from db/init.sql; validated up front so DB errors don't leak.
MAX_CUSTOMER_NAME_LEN = 120
MAX_CUSTOMER_PHONE_LEN = 25
MAX_DELIVERY_ADDRESS_LEN = 500

# track_order fields every caller may see. An allowlist, not a denylist: a
# column added to the query later stays hidden from non-admin callers until
# someone adds it here on purpose.
_PUBLIC_ORDER_FIELDS = ("order_id", "total_price", "order_status", "created_at", "updated_at")
_CUSTOMER_PII_FIELDS = ("customer_name", "customer_phone", "delivery_address")

_DB_UNAVAILABLE_ERROR = "Database temporarily unavailable. Please try again."

mcp = FastMCP("dialogflow-food-ordering-es")

_T = TypeVar("_T")


async def _run_blocking(fn: Callable[..., _T], *args: Any) -> _T:
    """Run blocking work (psycopg queries, Dialogflow gRPC calls) in a worker
    thread so the event loop keeps serving /health and other clients.

    FastMCP calls a plain `def` tool inline on the event loop, so every tool is
    an `async def` that delegates its blocking part here. The connection pool
    and the cached Dialogflow client are thread-safe (see database.get_pool and
    _sessions_client). If the caller is cancelled, the cancellation takes
    effect once the thread finishes, so a transaction is never left half-done."""
    return await anyio.to_thread.run_sync(functools.partial(fn, *args))


async def _run_db(fn: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    """`_run_blocking` for tool bodies that use the database.

    Any psycopg error (psycopg_pool.PoolTimeout is a subclass) becomes a
    generic error result, so driver text, which can name tables, columns,
    hosts or users, never reaches the client. The log line carries only the
    tool name, the exception class and the SQLSTATE."""
    try:
        return await _run_blocking(fn, *args)
    except psycopg.Error as exc:
        logger.warning(
            "database_error tool=%s error=%s sqlstate=%s",
            fn.__name__.lstrip("_"),
            type(exc).__name__,
            getattr(exc, "sqlstate", None) or "none",
        )
        return {"error": _DB_UNAVAILABLE_ERROR}


# ── Serialisation helper ───────────────────────────────────
def _serialise(obj: Any) -> Any:
    """Recursively convert Decimal to float and datetime to ISO format strings."""
    if obj is None:
        return None
    if isinstance(obj, list):
        return [_serialise(item) for item in obj]
    if isinstance(obj, dict):
        return {
            k: (
                float(v)
                if isinstance(v, Decimal)
                else v.isoformat()
                if isinstance(v, datetime)
                else _serialise(v)
                if isinstance(v, (dict, list))
                else v
            )
            for k, v in obj.items()
        }
    return obj


# ── Input helpers ───────────────────────────────────────────
def _clamp_limit(value: Any, default: int) -> int:
    """Coerce a list limit to an int within 1..MAX_LIST_LIMIT (bad values -> default)."""
    try:
        if isinstance(value, bool):
            raise TypeError("bool is not a valid limit")
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        n = default
    return max(1, min(n, MAX_LIST_LIMIT))


def _escape_like(text: str) -> str:
    r"""Escape LIKE/ILIKE wildcards so user input matches literally.

    Use together with `ESCAPE '\'` in the SQL."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_quantity(raw: Any) -> tuple[int | None, str | None]:
    """Validate a quantity. Returns (qty, None) or (None, error message)."""
    err = f"Quantity must be a whole number between 1 and {MAX_QTY_PER_ITEM}, got {raw!r}."
    if isinstance(raw, bool):
        return None, err
    try:
        if isinstance(raw, float):
            if not raw.is_integer():
                return None, err
            qty = int(raw)
        elif isinstance(raw, str):
            qty = int(raw.strip())
        else:
            qty = int(raw)
    except (TypeError, ValueError, OverflowError):
        return None, err
    if qty < 1 or qty > MAX_QTY_PER_ITEM:
        return None, err
    return qty, None


_ITEM_COLUMNS = "id, name, description, price, category, is_available"

QueryFn = Callable[[str, tuple[Any, ...]], list[dict[str, Any]]]


def _resolve_food_item(run_query: QueryFn, name: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve a user-supplied food name to exactly one food_items row.

    `run_query(sql, params)` must return a list of dict rows. Matching order:
    exact case-insensitive name first, then an escaped substring match that is
    only accepted when unambiguous. Returns (row, None) or (None, error)."""
    if not isinstance(name, str) or not name.strip():
        return None, "Food item name must be a non-empty string."
    clean = name.strip()

    exact = run_query(
        f"""SELECT {_ITEM_COLUMNS}
            FROM food_items
            WHERE lower(name) = lower(%s)
            ORDER BY id
            LIMIT 1""",
        (clean,),
    )
    if exact:
        return exact[0], None

    partial = run_query(
        f"""SELECT {_ITEM_COLUMNS}
            FROM food_items
            WHERE name ILIKE %s ESCAPE '\\'
            ORDER BY id
            LIMIT %s""",
        (f"%{_escape_like(clean)}%", MAX_MATCH_CANDIDATES + 1),
    )
    if len(partial) == 1:
        return partial[0], None
    if not partial:
        return None, f"Item '{clean}' is not recognized on our menu."
    names = [r["name"] for r in partial[:MAX_MATCH_CANDIDATES]]
    more = " (and more)" if len(partial) > MAX_MATCH_CANDIDATES else ""
    return None, (
        f"Item '{clean}' is ambiguous. Did you mean one of: "
        f"{', '.join(names)}{more}? Please use the exact name."
    )


# ═══════════════════════════════════════════════════════════
#  DIALOGFLOW NLU TOOLS
# ═══════════════════════════════════════════════════════════

_sessions_client_instance: dialogflow.SessionsClient | None = None
_sessions_client_endpoint: str | None = None
_sessions_client_lock = threading.Lock()


def _sessions_client() -> dialogflow.SessionsClient:
    """Return a cached SessionsClient for the configured (regional) endpoint.

    Created lazily on first use, never at import time. Recreated if the
    endpoint derived from the current LOCATION changes."""
    global _sessions_client_instance, _sessions_client_endpoint
    endpoint = (
        f"{LOCATION}-dialogflow.googleapis.com"
        if LOCATION and LOCATION != "global"
        else "dialogflow.googleapis.com"
    )
    with _sessions_client_lock:
        if _sessions_client_instance is None or _sessions_client_endpoint != endpoint:
            # Credentials from GOOGLE_APPLICATION_CREDENTIALS_JSON if set, else ADC.
            extra = client_kwargs()
            if endpoint == "dialogflow.googleapis.com":
                _sessions_client_instance = dialogflow.SessionsClient(**extra)
            else:
                _sessions_client_instance = dialogflow.SessionsClient(
                    client_options=ClientOptions(api_endpoint=endpoint), **extra
                )
            _sessions_client_endpoint = endpoint
        return _sessions_client_instance


def _session_path(
    session_id: str,
    project: str | None = None,
    location: str | None = None,
    environment: str | None = None,
) -> str:
    """Build the ES session resource path (defaults come from module config).

    Global:   projects/{p}/agent/sessions/{s}
    Regional: projects/{p}/locations/{l}/agent/sessions/{s}
    With an environment the tail becomes
              .../agent/environments/{e}/users/-/sessions/{s}"""
    project = PROJECT if project is None else project
    location = LOCATION if location is None else location
    environment = ENVIRONMENT if environment is None else environment
    if not project:
        raise ValueError("GCP_PROJECT environment variable is required to call Dialogflow.")

    parent = f"projects/{project}"
    if location and location != "global":
        parent += f"/locations/{location}"
    parent += "/agent"
    if environment:
        return f"{parent}/environments/{environment}/users/-/sessions/{session_id}"
    return f"{parent}/sessions/{session_id}"


@mcp.tool()
async def detect_intent(
    text: str,
    session_id: str | None = None,
    language_code: str = "en",
) -> dict:
    """Send a user message to the Dialogflow ES Food Ordering agent.
    Returns the bot reply (fulfillment text, may be empty), matched intent
    (new.order, order.add, order_remove, order.complete, track.order), extracted
    parameters (food_Item: list of exact menu names; number: list of quantities
    for order.add, or the order ID for track.order), and active contexts.
    Logs the conversation turn to the PostgreSQL database.
    session_id (optional): 1-36 characters of letters, digits, '-' or '_'."""
    return await _run_db(_detect_intent, text, session_id, language_code)


def _detect_intent(text: Any, session_id: Any, language_code: Any) -> dict[str, Any]:
    """Blocking body of detect_intent: validation, Dialogflow call, DB log."""
    if session_id is not None and (
        not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id)
    ):
        return {"error": "Invalid session_id: use 1-36 characters (letters, digits, '-' or '_')."}
    if not isinstance(text, str) or not text.strip():
        return {"error": "text must be a non-empty string."}
    if len(text) > MAX_QUERY_TEXT_LEN:
        return {"error": f"text must be at most {MAX_QUERY_TEXT_LEN} characters."}
    if not isinstance(language_code, str) or not LANGUAGE_CODE_RE.fullmatch(language_code):
        return {"error": "language_code must look like 'en' or 'en-US'."}
    sid = session_id or str(uuid.uuid4())
    session = _session_path(sid)
    client = _sessions_client()

    try:
        response = client.detect_intent(
            request=dialogflow.DetectIntentRequest(
                session=session,
                query_input=dialogflow.QueryInput(
                    text=dialogflow.TextInput(
                        text=text,
                        language_code=language_code,
                    ),
                ),
            )
        )
    except (GoogleAPICallError, RetryError) as exc:
        # Keep Google's error details (which can echo request data) out of
        # the tool result; log the type for operators.
        logger.warning("Dialogflow detect_intent failed: %s", type(exc).__name__)
        code = getattr(exc, "code", None)
        return {
            "error": "The language service is unavailable right now. Please try again.",
            "upstream_error": type(exc).__name__,
            "upstream_code": int(code) if isinstance(code, int) else None,
        }

    result = response.query_result

    contexts = [
        {
            "name": ctx.name.rsplit("/", 1)[-1],
            "lifespan": ctx.lifespan_count,
            "parameters": MessageToDict(ctx._pb).get("parameters", {}),
        }
        for ctx in result.output_contexts
    ]

    intent_name = result.intent.display_name if result.intent else None
    confidence = result.intent_detection_confidence
    fulfillment = result.fulfillment_text
    extracted_params = MessageToDict(result._pb).get("parameters", {})

    # Log interaction to PostgreSQL
    try:
        execute(
            """INSERT INTO conversation_logs
               (session_id, user_message, bot_response, intent, parameters, confidence)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                sid,
                text,
                fulfillment,
                intent_name,
                json.dumps(extracted_params),
                confidence,
            ),
        )
    except Exception as exc:
        # Logging the turn is best effort: the reply is still returned. Only
        # the exception class is logged (driver text can name hosts or users).
        logger.warning("conversation_log_failed session=%s error=%s", sid, type(exc).__name__)

    return {
        "session_id": sid,
        "fulfillment_text": fulfillment,
        "intent": intent_name,
        "confidence": confidence,
        "parameters": extracted_params,
        "contexts": contexts,
        "action": result.action or None,
        "all_required_params_present": result.all_required_params_present,
    }


# ═══════════════════════════════════════════════════════════
#  DATABASE TOOLS: SOUTH AFRICAN FOOD MENU
# ═══════════════════════════════════════════════════════════


@mcp.tool()
async def get_menu(category: str | None = None) -> dict:
    """Get the South African food menu with prices in ZAR (Rands).
    Includes Bunny Chow, Boerewors Roll, Bobotie, Vetkoek, Kota, Pap & Chakalaka,
    Malva Pudding, Milk Tart, and Sosaties.
    Optionally filter by category: 'Main', 'Street Food', 'Traditional', 'Dessert', 'Braai / Grill'."""
    return await _run_db(_get_menu, category)


def _get_menu(category: Any) -> dict[str, Any]:
    if isinstance(category, str) and category.strip():
        rows = fetch_all(
            """SELECT id, name, description, price, category, is_available
               FROM food_items
               WHERE is_available = TRUE AND category ILIKE %s ESCAPE '\\'
               ORDER BY id""",
            (f"%{_escape_like(category.strip())}%",),
        )
    else:
        rows = fetch_all(
            """SELECT id, name, description, price, category, is_available
               FROM food_items
               WHERE is_available = TRUE
               ORDER BY category, id"""
        )
    return {"menu": _serialise(rows), "total_items": len(rows)}


@mcp.tool()
async def get_food_item_details(food_name: str) -> dict:
    """Look up a specific food item on the menu by name (e.g. 'Bunny Chow', 'Kota')."""
    return await _run_db(_get_food_item_details, food_name)


def _get_food_item_details(food_name: Any) -> dict[str, Any]:
    row, error = _resolve_food_item(fetch_all, food_name)
    if error:
        return {"error": error}
    return {"item": _serialise(row)}


# ═══════════════════════════════════════════════════════════
#  DATABASE TOOLS: ORDER PLACEMENT & TRACKING
# ═══════════════════════════════════════════════════════════


def _order_id_error(order_id: Any) -> dict[str, Any] | None:
    """Reject ids Postgres can't hold (INTEGER) before they reach the database,
    so callers get a clean error instead of a raw psycopg exception."""
    if isinstance(order_id, bool) or not isinstance(order_id, int):
        return {"error": "order_id must be a whole number."}
    if not 1 <= order_id <= MAX_ORDER_ID:
        return {"error": f"Order #{order_id} not found. Please verify the order number."}
    return None


@mcp.tool()
async def track_order(order_id: int) -> dict:
    """Track the status of an existing food order by its Order ID.
    Returns status ('in progress', 'preparing', 'out for delivery', 'delivered', 'cancelled'),
    order items, and total amount in ZAR. Customer details (name, phone,
    delivery address) are returned to admin callers only."""
    # Order IDs are sequential, so any caller could enumerate them: customer
    # details go only to callers who pass the admin gate (and only when admin
    # tools are enabled at all). Decided here, on the event loop, where the
    # MCP request context is set.
    hide_pii = not ADMIN_TOOLS_ENABLED or _admin_denied() is not None
    return await _run_db(_track_order, order_id, hide_pii)


def _track_order(order_id: Any, hide_pii: bool) -> dict[str, Any]:
    if (bad_id := _order_id_error(order_id)) is not None:
        return bad_id
    order = fetch_one(
        """SELECT order_id, customer_name, customer_phone, total_price,
                  order_status, delivery_address, created_at, updated_at
           FROM orders
           WHERE order_id = %s""",
        (order_id,),
    )
    if not order:
        return {"error": f"Order #{order_id} not found. Please verify the order number."}

    if hide_pii:
        order = {k: order[k] for k in _PUBLIC_ORDER_FIELDS if k in order}

    items = fetch_all(
        """SELECT oi.quantity, oi.unit_price, oi.total_price, fi.name AS item_name
           FROM order_items oi
           JOIN food_items fi ON fi.id = oi.item_id
           WHERE oi.order_id = %s""",
        (order_id,),
    )

    return {
        "order": _serialise(order),
        "items": _serialise(items),
    }


class _OrderAborted(Exception):
    """Raised inside the order transaction to roll it back with a user-facing error."""


@mcp.tool()
async def complete_order(
    customer_name: str,
    customer_phone: str,
    items: list[dict],
    delivery_address: str = "Counter Collection",
) -> dict:
    """Complete and persist a new food order to the database.
    Items must be a list of dicts with 'name' and 'quantity'
    (e.g. [{'name': 'Bunny Chow', 'quantity': 2}, {'name': 'Milk Tart', 'quantity': 1}]).
    Quantity must be a whole number from 1 to 50 per item.
    Returns the newly generated order_id, itemized breakdown, and total price in ZAR."""
    return await _run_db(_complete_order, customer_name, customer_phone, items, delivery_address)


def _complete_order(
    customer_name: Any, customer_phone: Any, items: Any, delivery_address: Any
) -> dict[str, Any]:
    """Blocking body of complete_order: validation, then one DB transaction."""
    if not isinstance(items, list) or not items:
        return {
            "error": "Order must contain at least one item "
            "(a list of objects with 'name' and 'quantity')."
        }
    if not all(isinstance(entry, dict) for entry in items):
        return {"error": "Each order item must be an object with 'name' and 'quantity'."}
    if len(items) > MAX_ORDER_ENTRIES:
        return {"error": f"An order can have at most {MAX_ORDER_ENTRIES} item entries."}

    # Validate customer details (keeps raw psycopg errors away from clients)
    for label, value, max_len in (
        ("customer_name", customer_name, MAX_CUSTOMER_NAME_LEN),
        ("customer_phone", customer_phone, MAX_CUSTOMER_PHONE_LEN),
        ("delivery_address", delivery_address, MAX_DELIVERY_ADDRESS_LEN),
    ):
        if not isinstance(value, str) or not value.strip():
            return {"error": f"'{label}' must be a non-empty string."}
        if len(value.strip()) > max_len:
            return {"error": f"'{label}' must be at most {max_len} characters."}
    customer_name = customer_name.strip()
    customer_phone = customer_phone.strip()
    delivery_address = delivery_address.strip()

    # Validate names and quantities up front (no DB access needed), and merge
    # entries that repeat the same name (case-insensitive) so each distinct
    # name is looked up once inside the transaction.
    requested: dict[str, list[Any]] = {}  # casefolded name -> [display name, qty]
    for entry in items:
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            return {"error": "Each order item needs a non-empty 'name' string."}
        qty, qty_error = _parse_quantity(entry.get("quantity", 1))
        if qty_error:
            return {"error": f"Item '{name.strip()}': {qty_error}"}
        key = name.strip().casefold()
        if key in requested:
            requested[key][1] += qty
        else:
            requested[key] = [name.strip(), qty]
    if len(requested) > MAX_ORDER_LINES:
        return {"error": f"An order can have at most {MAX_ORDER_LINES} different items."}

    merged: dict[int, dict[str, Any]] = {}
    order_items_to_insert: list[dict[str, Any]] = []
    total_order_price = Decimal("0.00")

    # Item lookups, the order insert and all line-item inserts share ONE
    # connection and ONE transaction: any failure rolls everything back.
    try:
        with transaction() as cur:

            def run_query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
                cur.execute(sql, params)
                return cur.fetchall()

            # Resolve items and prices from DB
            for display_name, qty in requested.values():
                item_row, item_error = _resolve_food_item(run_query, display_name)
                if item_error or item_row is None:
                    raise _OrderAborted(item_error or f"Item '{display_name}' is not recognized.")
                if not item_row["is_available"]:
                    raise _OrderAborted(f"Item '{item_row['name']}' is currently out of stock.")

                # Merge repeated entries for the same item into one line
                # (order_items has UNIQUE (order_id, item_id)).
                line = merged.get(item_row["id"])
                if line is None:
                    line = {
                        "item_id": item_row["id"],
                        "name": item_row["name"],
                        "quantity": 0,
                        "unit_price": Decimal(str(item_row["price"])),
                    }
                    merged[item_row["id"]] = line
                line["quantity"] += qty
                if line["quantity"] > MAX_QTY_PER_ITEM:
                    raise _OrderAborted(
                        f"Item '{item_row['name']}': total quantity {line['quantity']} "
                        f"exceeds the maximum of {MAX_QTY_PER_ITEM} per item."
                    )

            for line in merged.values():
                line["total_price"] = line["unit_price"] * line["quantity"]
                total_order_price += line["total_price"]
                order_items_to_insert.append(line)

            # Insert order
            cur.execute(
                """INSERT INTO orders
                   (customer_name, customer_phone, total_price, order_status, delivery_address)
                   VALUES (%s, %s, %s, 'in progress', %s)
                   RETURNING order_id, total_price, order_status, created_at""",
                (customer_name, customer_phone, total_order_price, delivery_address),
            )
            new_order = cur.fetchone()
            if not new_order:
                raise _OrderAborted("Failed to create order record.")

            order_id = new_order["order_id"]

            # Insert all order line items in one statement (one round trip).
            placeholders = ", ".join(["(%s, %s, %s, %s, %s)"] * len(order_items_to_insert))
            params: list[Any] = []
            for line in order_items_to_insert:
                params.extend(
                    (
                        order_id,
                        line["item_id"],
                        line["quantity"],
                        line["unit_price"],
                        line["total_price"],
                    )
                )
            cur.execute(
                "INSERT INTO order_items (order_id, item_id, quantity, unit_price, total_price) "
                f"VALUES {placeholders}",
                tuple(params),
            )
    except _OrderAborted as exc:
        return {"error": str(exc)}

    return {
        "success": True,
        "order_id": order_id,
        "status": "in progress",
        "customer_name": customer_name,
        "delivery_address": delivery_address,
        "items": [
            {
                "name": line["name"],
                "quantity": line["quantity"],
                "unit_price": float(line["unit_price"]),
                "total_price": float(line["total_price"]),
            }
            for line in order_items_to_insert
        ],
        "total_price_zar": float(total_order_price),
        "message": f"Order #{order_id} placed successfully! Total: R{total_order_price:.2f}",
    }


# ═══════════════════════════════════════════════════════════
#  ADMIN TOOLS (registered only when MCP_ADMIN_TOOLS is enabled)
# ═══════════════════════════════════════════════════════════

_ADMIN_REQUIRED_ERROR = (
    "This is an admin tool. Reconnect with the admin token (MCP_ADMIN_TOKEN) to use it."
)

# Set by remote_server.create_remote_app(): in remote mode every admin call
# must carry a request that the auth middleware tagged with role "admin". A
# call with no request (the SDK stops attaching it, a background task, a
# direct call) is then refused instead of allowed.
_REMOTE_ROLE_REQUIRED = False

_P = ParamSpec("_P")

# Every function registered through `admin_tool` (the wrapped callables).
_ADMIN_TOOLS: list[Callable[..., Awaitable[dict[str, Any]]]] = []


def require_request_role() -> None:
    """Switch the admin gate to fail-closed mode (called by create_remote_app)."""
    global _REMOTE_ROLE_REQUIRED
    _REMOTE_ROLE_REQUIRED = True


def _current_request() -> Any | None:
    """The Starlette request behind the current MCP call, or None."""
    try:
        return mcp.get_context().request_context.request
    except (LookupError, ValueError):
        return None


def _current_role() -> str | None:
    """`request.state.mcp_role` of the current request, or None."""
    request = _current_request()
    if request is None:
        return None
    role = getattr(getattr(request, "state", None), "mcp_role", None)
    return role if isinstance(role, str) else None


def _admin_denied() -> dict[str, Any] | None:
    """Return a refusal dict unless the current caller may use admin tools.

    - HTTP request present: allowed only when the auth middleware in
      remote_server tagged it `request.state.mcp_role == "admin"`.
    - No request (stdio, direct Python calls such as the DB tests): allowed,
      unless remote mode requires a role, in which case refused (fail closed).
    """
    if _current_request() is None:
        return {"error": _ADMIN_REQUIRED_ERROR} if _REMOTE_ROLE_REQUIRED else None
    if _current_role() == "admin":
        return None
    return {"error": _ADMIN_REQUIRED_ERROR}


def admin_tool(
    fn: Callable[_P, Awaitable[dict[str, Any]]],
) -> Callable[_P, Awaitable[dict[str, Any]]]:
    """The only way to register an admin tool: gate it, record it, expose it.

    The gate runs on the event loop, before any blocking work is handed to a
    worker thread, because that is where the MCP request context is set. A
    refusal logs one WARNING line `admin_tool_denied tool=<name> role=<role>`
    and never touches the database."""

    @functools.wraps(fn)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> dict[str, Any]:
        # Looked up as a module global at call time so tests can patch it.
        denied = _admin_denied()
        if denied is not None:
            logger.warning(
                "admin_tool_denied tool=%s role=%s", fn.__name__, _current_role() or "none"
            )
            return denied
        return await fn(*args, **kwargs)

    _ADMIN_TOOLS.append(wrapper)
    if ADMIN_TOOLS_ENABLED:
        mcp.tool()(wrapper)
    return wrapper


# Customer tools use `@mcp.tool()`; admin tools must use `@admin_tool`. The
# tool inventory test in tests/test_admin_gate.py fails for any registered
# tool that is in neither its PUBLIC_TOOLS nor its ADMIN_TOOLS set.
ORDER_STATUSES = ("in progress", "preparing", "out for delivery", "delivered", "cancelled")


@admin_tool
async def update_order_status(order_id: int, new_status: str) -> dict[str, Any]:
    """Set an order's status. Allowed statuses: 'in progress', 'preparing',
    'out for delivery', 'delivered', 'cancelled'. Any allowed status can be set
    at any time; transitions are not enforced."""
    return await _run_db(_update_order_status, order_id, new_status)


def _update_order_status(order_id: Any, new_status: Any) -> dict[str, Any]:
    if (bad_id := _order_id_error(order_id)) is not None:
        return bad_id
    status_clean = new_status.strip().lower() if isinstance(new_status, str) else ""
    if status_clean not in ORDER_STATUSES:
        return {
            "error": f"Invalid status '{new_status}'. Allowed: {', '.join(sorted(ORDER_STATUSES))}"
        }

    affected = execute(
        """UPDATE orders
           SET order_status = %s, updated_at = NOW()
           WHERE order_id = %s""",
        (status_clean, order_id),
    )
    if affected == 0:
        return {"error": f"Order #{order_id} not found."}

    return {
        "success": True,
        "order_id": order_id,
        "new_status": status_clean,
        "message": f"Order #{order_id} updated to '{status_clean}'.",
    }


@admin_tool
async def get_recent_orders(limit: int = 10, status: str | None = None) -> dict[str, Any]:
    """List recent food orders, optionally filtered by status.
    `limit` is clamped to 1..100."""
    return await _run_db(_get_recent_orders, limit, status)


def _get_recent_orders(limit: Any, status: Any) -> dict[str, Any]:
    limit = _clamp_limit(limit, 10)
    status = status.strip().lower() if isinstance(status, str) else None
    if status:
        rows = fetch_all(
            """SELECT order_id, customer_name, customer_phone, total_price,
                      order_status, created_at
               FROM orders
               WHERE order_status = %s
               ORDER BY created_at DESC LIMIT %s""",
            (status, limit),
        )
    else:
        rows = fetch_all(
            """SELECT order_id, customer_name, customer_phone, total_price,
                      order_status, created_at
               FROM orders
               ORDER BY created_at DESC LIMIT %s""",
            (limit,),
        )
    return {"orders": _serialise(rows), "count": len(rows)}


@admin_tool
async def get_conversation_logs(session_id: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Retrieve logged conversation turns from Dialogflow chats.
    Filter by session_id to view a full multi-turn conversation trace.
    `limit` is clamped to 1..100."""
    return await _run_db(_get_conversation_logs, session_id, limit)


def _get_conversation_logs(session_id: Any, limit: Any) -> dict[str, Any]:
    limit = _clamp_limit(limit, 20)
    if session_id:
        rows = fetch_all(
            """SELECT id, session_id, user_message, bot_response, intent, parameters, confidence, created_at
               FROM conversation_logs
               WHERE session_id = %s
               ORDER BY created_at ASC LIMIT %s""",
            (session_id, limit),
        )
    else:
        rows = fetch_all(
            """SELECT id, session_id, user_message, bot_response, intent, parameters, confidence, created_at
               FROM conversation_logs
               ORDER BY created_at DESC LIMIT %s""",
            (limit,),
        )
    return {"logs": _serialise(rows), "count": len(rows)}


if __name__ == "__main__":
    mcp.run()
