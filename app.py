"""Hugging Face Spaces (Gradio SDK) entry point for Agent QA.

Thin wrapper only: the existing FastAPI application is mounted unchanged.
A minimal Gradio UI is added at /ui; the REST API stays at the root.
"""

from __future__ import annotations

import sys
from pathlib import Path

import gradio as gr

# The agent_qa package lives in core/ and is not installed in the Space image,
# so put it on the import path before importing it.
sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))

from fastapi.responses import RedirectResponse  # noqa: E402

from agent_qa.api import app as fastapi_app  # noqa: E402

with gr.Blocks(title="Agent QA") as demo:
    gr.Markdown("# Agent QA API is running")
    gr.Markdown(
        "The REST API is served from this same Space: "
        "[`/health`](/health) &middot; [`/docs`](/docs) &middot; "
        "[`/openapi.json`](/openapi.json)"
    )

# mount_gradio_app returns the same FastAPI app; the Gradio UI lives at /ui.
app = gr.mount_gradio_app(fastapi_app, demo, path="/ui")


@app.get("/", include_in_schema=False)
def _root() -> RedirectResponse:
    """Send the Space landing URL to the Gradio UI; the API keeps its own paths."""
    return RedirectResponse(url="/ui/")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=7860)
