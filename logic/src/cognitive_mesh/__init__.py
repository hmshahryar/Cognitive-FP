def main() -> None:
    """Run the API in development: `uv run cognitive-mesh`."""
    import uvicorn

    uvicorn.run("cognitive_mesh.main:app", host="127.0.0.1", port=8000, reload=True)
