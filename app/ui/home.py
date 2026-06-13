import httpx
import streamlit as st
from app.config import settings

st.set_page_config(page_title="PH Law RAG", layout="wide")
st.title("PH Law RAG")

with st.sidebar:
    st.header("Settings")
    debug = st.toggle("Debug Mode", value=settings.debug)
    st.caption(f"Model: {settings.llm_model}")
    st.caption(f"dense_top_k: {settings.dense_top_k}")
    st.caption(f"rerank_top_n: {settings.rerank_top_n}")
    st.caption(f"min_chunks_for_answer: {settings.min_chunks_for_answer}")

chat_tab, sources_tab = st.tabs(["Chat", "Sources"])
prompt = st.chat_input("Ask a question about Philippine law")

with chat_tab:
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
           st.markdown(m["content"])

    if prompt:
        st.session_state.messages.append({ "role": "user", "content": prompt })
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Retrieving and generating..."):
                try:
                    resp = httpx.post(
                        f"{settings.api_base_url}/query/ask",
                        json={"question": prompt, "debug": debug},
                        timeout=120,
                    )
                    resp.raise_for_status()
                    result = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    result = {
                        "answer": f"API request failed: {exc}",
                        "sources": [],
                    }
            st.markdown(result["answer"])
            if result["sources"]:
                st.markdown("**Sources:**")
                for s in result["sources"]:
                    line = f"[{s['ref']}] [{s['title']}]({s['url']})"
                    if s.get("locator"):
                        line += f" — {s['locator']}"
                    if s.get("via"):
                        line += f"  _({s['via']})_"
                    st.markdown(line)
            if result.get('debug'):
                with st.expander("Debug trace"):
                    st.json(result["debug"])
        st.session_state.messages.append(
            {"role": "assistant", "content": result["answer"]}
        )

with sources_tab:
    try:
        resp = httpx.get(f"{settings.api_base_url}/documents", timeout=10)
        resp.raise_for_status()
        docs = resp.json()["documents"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        st.error(f"Could not load documents: {exc}")
        docs = []
    st.caption(f"{len(docs)} documents indexed")
    st.dataframe(docs, width="stretch")
