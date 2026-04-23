from fastapi import APIRouter

router = APIRouter(prefix="/query", tags=["query"])

@router.post("/ask")
def ask(request):
	return { "message": "hello world" }
