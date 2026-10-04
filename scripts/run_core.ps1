# Run core Agent QA service
.\..\core\.venv\Scripts\Activate.ps1
python -m uvicorn agent_qa.api:app --host 127.0.0.1 --port 8765
