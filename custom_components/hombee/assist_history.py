"""Bridge native HA tool-result history across the supported HA releases."""

from homeassistant.components import conversation
from homeassistant.helpers import llm


class ToolResultAdapter:
    """Select the HA result API once, without using deprecated result properties."""

    def __init__(self, content_type, result_type) -> None:
        self._content_type = content_type
        self._result_type = result_type

    def create(self, agent_id, call_id, name, data):
        """Create cloud history in the native result representation."""
        result = (
            {"tool_result": data}
            if self._result_type is None
            else {"result": self._result_type(data=data, error=bool(data.get("error")))}
        )
        return self._content_type(
            agent_id=agent_id, tool_call_id=call_id, tool_name=name, **result
        )

    def data(self, content):
        """Read both native and external results without a deprecated API call."""
        return content.tool_result if self._result_type is None else content.result.data


# HA 2026.10 introduced llm.ToolResult; 2026.9 stores the result dictionary directly.
TOOL_RESULTS = ToolResultAdapter(
    conversation.ToolResultContent, getattr(llm, "ToolResult", None)
)
