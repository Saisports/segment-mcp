"""MCP server for the Segment Public API.

Env:
  SEGMENT_API_TOKEN   Public API bearer token (required)
  SEGMENT_REGION      us (default) | eu
  SEGMENT_MCP_MODE    read (default) | write  -- write enables mutating tools
"""
import os
import re
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BASES = {"us": "https://api.segmentapis.com", "eu": "https://eu1.api.segmentapis.com"}
mcp = FastMCP("segment")


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9_\-/]*$")


def _id(value: str, what: str = "id") -> str:
    """Reject anything that is not a plain Segment id, so tool args cannot alter the URL path
    (e.g. '../destinations/x' or '?x=1') when interpolated into a request path."""
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {what}: expected letters, digits, '_' or '-'")
    return value


def _path(value: str) -> str:
    if not value.startswith("/"):
        value = "/" + value
    if ".." in value or "//" in value or not _PATH_RE.fullmatch(value):
        raise ValueError("invalid path: only letters, digits, '_', '-' and '/' are allowed")
    return value


def _client() -> httpx.Client:
    token = os.environ.get("SEGMENT_API_TOKEN")
    if not token:
        raise RuntimeError("SEGMENT_API_TOKEN is not set")
    base = BASES[os.environ.get("SEGMENT_REGION", "us").lower()]
    return httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30,
    )


_KEEP_IN_METADATA = {"id", "name", "slug", "status", "isCloudEventSource"}


def _slim(x: Any) -> Any:
    """Reduce embedded catalog metadata to id/name/slug/status (actions alone can be 300KB+)."""
    if isinstance(x, list):
        return [_slim(i) for i in x]
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if k == "metadata" and isinstance(v, dict):
                v = {mk: mv for mk, mv in v.items() if mk in _KEEP_IN_METADATA}
            out[k] = _slim(v)
        return out
    return x


def _call(method: str, path: str, params: dict | None = None, body: Any = None, full: bool = False) -> Any:
    with _client() as c:
        r = c.request(method, path, params={k: v for k, v in (params or {}).items() if v is not None}, json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"Segment API {r.status_code}: {r.text[:500]}")
    data = r.json() if r.content else {}
    return data if full else _slim(data)


def _page(count: int, cursor: str | None) -> dict:
    return {"pagination[count]": count, "pagination[cursor]": cursor}


def _require_write() -> None:
    if os.environ.get("SEGMENT_MCP_MODE", "read").lower() != "write":
        raise RuntimeError("Write tools disabled. Set SEGMENT_MCP_MODE=write to enable.")


# ---------- read tools ----------
@mcp.tool()
def get_workspace() -> Any:
    """Get workspace info (name, slug, id)."""
    return _call("GET", "/")


@mcp.tool()
def list_sources(count: int = 100, cursor: str | None = None) -> Any:
    """List sources. Use pagination.next from the result as cursor for the next page."""
    return _call("GET", "/sources", _page(count, cursor))


@mcp.tool()
def get_source(source_id: str) -> Any:
    """Get one source."""
    return _call("GET", f"/sources/{_id(source_id, "source_id")}")


@mcp.tool()
def list_connected_destinations(source_id: str, count: int = 100, cursor: str | None = None) -> Any:
    """List destinations connected to a source."""
    return _call("GET", f"/sources/{_id(source_id, "source_id")}/connected-destinations", _page(count, cursor))


@mcp.tool()
def list_connected_warehouses(source_id: str) -> Any:
    """List warehouses connected to a source."""
    return _call("GET", f"/sources/{_id(source_id, "source_id")}/connected-warehouses")


@mcp.tool()
def get_source_settings(source_id: str) -> Any:
    """Get a source's settings."""
    return _call("GET", f"/sources/{_id(source_id, "source_id")}/settings")


@mcp.tool()
def list_destinations(count: int = 100, cursor: str | None = None) -> Any:
    """List destinations."""
    return _call("GET", "/destinations", _page(count, cursor))


@mcp.tool()
def get_destination(destination_id: str) -> Any:
    """Get one destination."""
    return _call("GET", f"/destinations/{_id(destination_id, "destination_id")}")


@mcp.tool()
def list_destination_subscriptions(destination_id: str, count: int = 100, cursor: str | None = None) -> Any:
    """List destination action subscriptions (mappings/triggers)."""
    return _call("GET", f"/destinations/{_id(destination_id, "destination_id")}/subscriptions", _page(count, cursor))


@mcp.tool()
def destination_delivery_metrics(
    destination_id: str, source_id: str, start_time: str, end_time: str, granularity: str = "HOUR"
) -> Any:
    """Delivery metrics summary for a source->destination pair. Times are ISO-8601 UTC."""
    return _call(
        "GET",
        f"/destinations/{_id(destination_id, "destination_id")}/delivery-metrics",
        {"sourceId": source_id, "startTime": start_time, "endTime": end_time, "granularity": granularity},
    )


_DELIVERY = {
    "failed_delivery": "failed-delivery",
    "successful_delivery": "successful-delivery",
    "filtered_at_destination": "filtered-at-destination",
    "filtered_at_source": "filtered-at-source",
    "failed_on_ingest": "failed-on-ingest",
    "successfully_received": "successfully_received".replace("_", "-"),
}


@mcp.tool()
def delivery_overview(
    metric: str, source_id: str, start_time: str, end_time: str,
    granularity: str = "HOUR", destination_id: str | None = None,
) -> Any:
    """Delivery Overview pipeline metrics. metric is one of: failed_delivery, successful_delivery,
    filtered_at_destination, filtered_at_source, failed_on_ingest, successfully_received.
    destination_id is needed for the destination-side metrics."""
    if metric not in _DELIVERY:
        raise ValueError(f"metric must be one of {sorted(_DELIVERY)}")
    return _call(
        "GET",
        f"/delivery-overview/{_DELIVERY[metric]}",
        {"sourceId": source_id, "destinationConfigId": destination_id,
         "startTime": start_time, "endTime": end_time, "granularity": granularity},
    )


@mcp.tool()
def list_tracking_plans(count: int = 100, cursor: str | None = None) -> Any:
    """List tracking plans."""
    return _call("GET", "/tracking-plans", _page(count, cursor))


@mcp.tool()
def get_tracking_plan(tracking_plan_id: str) -> Any:
    """Get a tracking plan."""
    return _call("GET", f"/tracking-plans/{_id(tracking_plan_id, "tracking_plan_id")}")


@mcp.tool()
def list_tracking_plan_rules(tracking_plan_id: str, count: int = 200, cursor: str | None = None) -> Any:
    """List rules (events/properties) in a tracking plan."""
    return _call("GET", f"/tracking-plans/{_id(tracking_plan_id, "tracking_plan_id")}/rules", _page(count, cursor))


@mcp.tool()
def list_tracking_plan_sources(tracking_plan_id: str) -> Any:
    """List sources attached to a tracking plan."""
    return _call("GET", f"/tracking-plans/{_id(tracking_plan_id, "tracking_plan_id")}/sources")


@mcp.tool()
def list_reverse_etl_models(count: int = 100, cursor: str | None = None) -> Any:
    """List Reverse ETL models."""
    return _call("GET", "/reverse-etl-models", _page(count, cursor))


@mcp.tool()
def get_reverse_etl_sync_status(model_id: str, sync_id: str) -> Any:
    """Get status of a Reverse ETL sync."""
    return _call("GET", f"/reverse-etl-models/{_id(model_id, "model_id")}/syncs/{_id(sync_id, "sync_id")}")


@mcp.tool()
def list_warehouses() -> Any:
    """List warehouses."""
    return _call("GET", "/warehouses")


@mcp.tool()
def list_profiles_warehouses(space_id: str) -> Any:
    """List Profiles Sync warehouses for a space (spa_...)."""
    return _call("GET", f"/spaces/{_id(space_id, "space_id")}/profiles-warehouses")


@mcp.tool()
def list_functions(resource_type: str = "DESTINATION", count: int = 100, cursor: str | None = None) -> Any:
    """List Functions. resource_type: SOURCE | DESTINATION | INSERT_DESTINATION | INSERT_TRANSFORMATION."""
    return _call("GET", "/functions", {"resourceType": resource_type, **_page(count, cursor)})


@mcp.tool()
def list_transformations(count: int = 100, cursor: str | None = None) -> Any:
    """List Protocols transformations."""
    return _call("GET", "/transformations", _page(count, cursor))


@mcp.tool()
def list_audit_events(
    start_time: str | None = None, end_time: str | None = None,
    resource_type: str | None = None, resource_id: str | None = None,
    count: int = 100, cursor: str | None = None,
) -> Any:
    """Audit trail events, optionally filtered by time or resource."""
    return _call("GET", "/audit-events", {
        "startTime": start_time, "endTime": end_time, "resourceType": resource_type,
        "resourceId": resource_id, "count": count, "cursor": cursor})


@mcp.tool()
def api_get(path: str, params: dict | None = None, full: bool = False) -> Any:
    """Escape hatch: GET any Public API path (e.g. /labels, /usage/mtu). Always read-only.
    Responses are slimmed unless full=True."""
    return _call("GET", _path(path), params, full=full)


@mcp.tool()
def find_in_catalog(kind: str, query: str, count: int = 200) -> Any:
    """Search the Segment catalog by name/slug. kind: sources | destinations | warehouses.
    Returns id/name/slug (use the id as metadata_id when creating)."""
    if kind not in ("sources", "destinations", "warehouses"):
        raise ValueError("kind must be sources, destinations or warehouses")
    q = query.lower()
    out: list[dict] = []
    cursor: str | None = None
    for _ in range(50):  # the catalog is large and paginated; walk every page
        data = _call("GET", f"/catalog/{kind}", _page(count, cursor), full=True).get("data", {})
        items = next((v for v in data.values() if isinstance(v, list)), [])
        out += [
            {"id": i.get("id"), "name": i.get("name"), "slug": i.get("slug"), "status": i.get("status")}
            for i in items if q in (str(i.get("name", "")) + " " + str(i.get("slug", ""))).lower()
        ]
        cursor = (data.get("pagination") or {}).get("next")
        if not cursor:
            break
    return out


@mcp.tool()
def get_catalog_destination(metadata_id: str) -> Any:
    """Full catalog entry for a destination type, including its required settings (options)."""
    return _call("GET", f"/catalog/destinations/{_id(metadata_id, "metadata_id")}", full=True)


@mcp.tool()
def get_warehouse_connection_state(warehouse_id: str) -> Any:
    """Check whether Segment can currently connect to a warehouse."""
    return _call("GET", f"/warehouses/{_id(warehouse_id, "warehouse_id")}/connection-state")


# ---------- write tools (SEGMENT_MCP_MODE=write) ----------
@mcp.tool()
def set_destination_enabled(destination_id: str, enabled: bool) -> Any:
    """WRITE: enable or disable a destination."""
    _require_write()
    return _call("PATCH", f"/destinations/{_id(destination_id, "destination_id")}", body={"enabled": enabled})


@mcp.tool()
def list_catalog_destination_actions(metadata_id: str) -> Any:
    """Compact list of a destination type's actions: id, slug, name, default trigger, and field names."""
    d = _call("GET", f"/catalog/destinations/{_id(metadata_id, "metadata_id")}", full=True)["data"]
    dest = d.get("destination") or d.get("destinationMetadata") or next(iter(d.values()))
    return [
        {"id": a.get("id"), "slug": a.get("slug"), "name": a.get("name"),
         "defaultTrigger": a.get("defaultTrigger"),
         "fields": [f.get("fieldKey") for f in a.get("fields", [])]}
        for a in dest.get("actions", [])
    ]


@mcp.tool()
def create_destination_subscription(
    destination_id: str, action_id: str, name: str, trigger: str, settings: dict, enabled: bool = True
) -> Any:
    """WRITE: create an Actions subscription (mapping). trigger is a Segment filter such as
    'type = "track"'. settings holds the action's field mappings (e.g. url, method, data)."""
    _require_write()
    return _call("POST", f"/destinations/{_id(destination_id, "destination_id")}/subscriptions", body={
        "name": name, "actionId": action_id, "trigger": trigger,
        "settings": settings, "enabled": enabled})


@mcp.tool()
def create_source(slug: str, metadata_id: str, enabled: bool = True, settings: dict | None = None) -> Any:
    """WRITE: create a source. metadata_id comes from find_in_catalog('sources', ...)."""
    _require_write()
    return _call("POST", "/sources", body={
        "slug": slug, "enabled": enabled, "metadataId": metadata_id, "settings": settings or {}})


@mcp.tool()
def create_destination(
    source_id: str, metadata_id: str, name: str, enabled: bool = True, settings: dict | None = None
) -> Any:
    """WRITE: create a destination attached to a source. See get_catalog_destination for settings."""
    _require_write()
    return _call("POST", "/destinations", body={
        "sourceId": source_id, "metadataId": metadata_id, "name": name,
        "enabled": enabled, "settings": settings or {}})


@mcp.tool()
def connect_source_to_warehouse(warehouse_id: str, source_id: str) -> Any:
    """WRITE: connect a source to an existing warehouse."""
    _require_write()
    return _call("POST", f"/warehouses/{_id(warehouse_id, "warehouse_id")}/connected-sources", body={"sourceId": source_id})


@mcp.tool()
def delete_source(source_id: str, confirm: bool = False) -> Any:
    """WRITE: delete a source (irreversible). Requires confirm=True."""
    _require_write()
    if not confirm:
        raise RuntimeError("Refusing to delete without confirm=True")
    return _call("DELETE", f"/sources/{_id(source_id, "source_id")}")


@mcp.tool()
def delete_destination(destination_id: str, confirm: bool = False) -> Any:
    """WRITE: delete a destination (irreversible). Requires confirm=True."""
    _require_write()
    if not confirm:
        raise RuntimeError("Refusing to delete without confirm=True")
    return _call("DELETE", f"/destinations/{_id(destination_id, "destination_id")}")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
