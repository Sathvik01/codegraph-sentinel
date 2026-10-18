"""D8 CLI wiring: two piped inputs -> exit 0, both turns recorded in the thread.

Wiring only, per plan — the model is a scripted fake (no live LLM), and the
assertions are about the REPL plumbing: input loop, run_turn per line, and
the thread accumulating both turns via the checkpointer.
"""

import uuid

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import Field

from codegraph_sentinel import cli
from codegraph_sentinel.agent_graph import build_graph, close_graph


class FakeToolModel(GenericFakeChatModel):
    """Scripted model with a call counter (bind_tools override required)."""

    counter: dict = Field(default_factory=lambda: {"calls": 0})

    @property
    def call_count(self) -> int:
        return self.counter["calls"]

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002 - scripted
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.counter["calls"] += 1
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


async def test_two_piped_inputs_exit_zero_and_land_in_thread(gw_ingested, mcp_app):
    thread = f"cli-{uuid.uuid4().hex[:8]}"
    model = FakeToolModel(
        messages=iter([AIMessage("Sentinel ready."), AIMessage("Seven functions known.")])
    )

    code = await cli.main(
        ["What functions exist?", "How many was that?"],
        thread_id=thread,
        model=model,
        gateway=gw_ingested,
    )
    assert code == 0
    assert model.call_count == 2  # one model turn per piped input

    # both turns recorded in the SAME thread, in order
    graph = await build_graph(gw_ingested, mcp_app, model=model)
    try:
        state = await graph.aget_state({"configurable": {"thread_id": thread}})
        humans = [m.content for m in state.values["messages"] if isinstance(m, HumanMessage)]
        assert humans == ["What functions exist?", "How many was that?"]
    finally:
        await close_graph(graph)


async def test_cli_empty_input_exits_zero_without_consuming_model(gw_ingested, mcp_app):
    thread = f"cli-{uuid.uuid4().hex[:8]}"
    model = FakeToolModel(messages=iter([AIMessage("never used")]))
    code = await cli.main([], thread_id=thread, model=model, gateway=gw_ingested)
    assert code == 0
    assert model.call_count == 0  # empty session spends nothing
