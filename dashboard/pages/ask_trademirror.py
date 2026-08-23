from __future__ import annotations

from html import escape
from typing import Any

from dashboard.ask_trademirror import answer_question, provider_from_environment
from dashboard.pages.common import page_header


SUGGESTED_QUESTIONS = (
    "What patterns hurt my historical results?",
    "What appeared to help?",
    "Did options and equities perform differently?",
    "Did I hold losing trades longer?",
    "Were losses concentrated?",
    "Why was some data excluded?",
)


def render(st: Any, data: Any) -> None:
    page_header(st, data, "Ask TradeMirror", "Grounded explanations of your validated historical behavioral evidence.")
    provider = provider_from_environment()
    st.markdown(
        f"<div class='tm-note'><strong>{escape(provider.mode_label)}</strong><br>"
        "Ask TradeMirror explains deterministic TradeMirror evidence. It does not calculate returns, predict outcomes, or recommend trades.</div>",
        unsafe_allow_html=True,
    )
    if provider.provider_name == "deterministic":
        st.info("No OpenAI API key is configured. The synthetic demo uses deterministic, pre-authored explanations grounded in the same evidence package.")
        with st.expander("Enable OpenAI locally"):
            st.code("$env:OPENAI_API_KEY = \"your-key\"\n$env:OPENAI_MODEL = \"gpt-5.6-terra\"", language="powershell")

    st.subheader("Suggested questions")
    columns = st.columns(2)
    for index, question in enumerate(SUGGESTED_QUESTIONS):
        with columns[index % 2]:
            if st.button(question, key=f"ask-suggested-{index}", use_container_width=True):
                st.session_state["ask_trademirror_question"] = question

    question = st.chat_input("Ask about historical behavioral evidence")
    if question:
        st.session_state["ask_trademirror_question"] = question
    selected = st.session_state.get("ask_trademirror_question")
    if not selected:
        st.info("Choose a suggested question or enter your own to generate a grounded explanation.")
        return
    history = tuple(st.session_state.get("ask_trademirror_history", [])[-6:])
    response = answer_question(data, selected, history=history)
    _render_response(st, selected, response)
    updated_history = [*history, {"role": "user", "content": selected}, {"role": "assistant", "content": response["answer"]}]
    st.session_state["ask_trademirror_history"] = updated_history[-12:]


def _render_response(st: Any, question: str, response: dict[str, Any]) -> None:
    st.chat_message("user").write(question)
    with st.chat_message("assistant"):
        st.write(response["answer"])
        meta = f"Confidence: {response['confidence']} · Mode: {response['mode_label']}"
        st.caption(meta)
        if response.get("process_guardrail"):
            st.markdown(f"**Process guardrail:** {response['process_guardrail']}")
        if response.get("limitations"):
            st.markdown("**Limitations**")
            for limitation in response["limitations"]:
                st.caption(str(limitation))
        if response.get("follow_up_questions"):
            st.markdown("**Follow-up questions**")
            for follow_up in response["follow_up_questions"][:3]:
                st.caption(str(follow_up))
        with st.expander("Evidence used"):
            evidence_rows = [
                {
                    "Evidence ID": item["evidence_id"],
                    "Title": item["title"],
                    "Summary": item["summary"],
                }
                for item in response.get("evidence", [])
            ]
            if evidence_rows:
                st.dataframe(evidence_rows, hide_index=True, use_container_width=True)
            else:
                st.write("No evidence was used because the question was outside TradeMirror scope.")
