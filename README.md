# segment-mcp

An MCP server for the [Twilio Segment Public API](https://docs.segmentapis.com). It is read-first: write tools are disabled unless you opt in.

Segment has no official MCP for managing workspaces. The Twilio docs MCP only searches documentation (including Segment docs). This server operates on a real workspace: sources, destinations, delivery metrics, tracking plans, warehouses and more.

## Setup

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

1. Create a Public API token: Segment → Settings → Workspace settings → Access Management → Tokens. Prefer a role with the least access you need.
2. Register the server with Claude Code (run in your own shell, so the token stays out of chat):

   ```
   claude mcp add segment \
     -e SEGMENT_API_TOKEN=<token> \
     -e SEGMENT_REGION=us \
     -e SEGMENT_MCP_MODE=read \
     -- uv run --directory /path/to/segment-mcp segment-mcp
   ```

   Check `~/.claude.json` afterwards: make sure `segment-mcp` is the last entry in `args`. A lost `--` separator drops it.
3. Restart Claude Code and run `/mcp` to confirm `segment` is connected.

| Variable | Values | Meaning |
|---|---|---|
| `SEGMENT_API_TOKEN` | token | Required |
| `SEGMENT_REGION` | `us` (default), `eu` | Picks `api.segmentapis.com` or `eu1.api.segmentapis.com` |
| `SEGMENT_MCP_MODE` | `read` (default), `write` | `write` enables the mutating tools |

## Tools

**Read** (always available): `get_workspace`, `list_sources`, `get_source`, `list_connected_destinations`, `list_connected_warehouses`, `get_source_settings`, `list_destinations`, `get_destination`, `list_destination_subscriptions`, `destination_delivery_metrics`, `delivery_overview`, `list_tracking_plans`, `get_tracking_plan`, `list_tracking_plan_rules`, `list_tracking_plan_sources`, `list_reverse_etl_models`, `get_reverse_etl_sync_status`, `list_warehouses`, `get_warehouse_connection_state`, `list_profiles_warehouses`, `list_functions`, `list_transformations`, `list_audit_events`, `find_in_catalog`, `get_catalog_destination`, `list_catalog_destination_actions`, `api_get` (read-only GET on any path).

**Write** (need `SEGMENT_MCP_MODE=write`): `create_source`, `create_destination`, `create_destination_subscription`, `connect_source_to_warehouse`, `set_destination_enabled`, `delete_source`, `delete_destination`.

Responses are slimmed: embedded catalog metadata is reduced to `id`, `name`, `slug`, `status`. A destinations list drops from about 450 KB to 11 KB. Pass `full=True` to `api_get` for raw output.

## Example: cloud pipeline

1. `find_in_catalog("sources", "http")` → HTTP API metadata id.
2. `create_source(slug, metadata_id)`.
3. `find_in_catalog("destinations", "webhook")` → `Webhooks (Actions)`.
4. `create_destination(source_id, metadata_id, name)`.
5. `list_catalog_destination_actions(metadata_id)` → the `Send` action id.
6. `create_destination_subscription(destination_id, action_id, name, trigger='type = "track"', settings={"url": ..., "method": "POST", "data": {"@path": "$."}})`.
7. Send a track event to `https://api.segment.io/v1/track` (write key as basic-auth user), then confirm delivery with `delivery_overview`.

## Things learned

- Actions destinations do nothing until they have a subscription.
- `find_in_catalog` has to walk every catalog page; the first page misses most entries.
- A warehouse sync that starts before any events arrive reports success with 0 rows. That is not a failure.
- `get_warehouse_connection_state` can report FAILED while syncs still work; judge by sync history.
- Some sources return the literal placeholder `{AWS_SECRET_KEY}` as write key. Get the real key from the Segment UI.
- Endpoint paths come from the official Go SDK (`segmentio/public-api-sdk-go`). A few parameter names, such as `destinationConfigId` for Delivery Overview, are unverified.

## Safety

- Use a token scoped to a role you can live with. Rotate it if it leaks.
- Keep write mode off unless you are making changes, and review each write call.
- Keep keys in files or env vars, not in chat. Example: `read -rs K && printf '%s' "$K" > ~/.segment_test_key && chmod 600 ~/.segment_test_key`.
- Delete tools are irreversible.

## Development

```
uv sync
uv run python -c "import asyncio, segment_mcp.server as s; print(len(asyncio.run(s.mcp.list_tools())))"
```

Pinned to `mcp<2`: v2 renamed `FastMCP`.
