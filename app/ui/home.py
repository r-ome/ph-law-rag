import streamlit as st
from app.retriever.answer_service import answer
from app.db import list_documents
from app.config import settings

st.set_page_config(page_title="PH Law RAG", layout="wide")
st.title("PH Law RAG")

with st.sidebar:
    st.header("Settings")
    debug = st.toggle("Debug Mode", value=settings.debug)
    settings.debug = debug
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
                result = answer(prompt)
            st.markdown(result["answer"])
            if result["sources"]:
                st.markdown("**Sources:**")
                for s in result["sources"]:
                    st.markdown(f"[{s['ref']}] [{s['title']}]({s['url']})")
            if result.get('debug'):
                with st.expander("Debug trace"):
                    st.json(result["debug"])
        st.session_state.messages.append(
            {"role": "assistant", "content": result["answer"]}
        )

with sources_tab:
    docs = list_documents()
    st.caption(f"{len(docs)} documents indexed")
    st.dataframe(docs, use_container_width=True)
