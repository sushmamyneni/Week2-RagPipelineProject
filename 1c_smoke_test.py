from dotenv import load_dotenv
import os, yaml
from langchain_nebius import ChatNebius

load_dotenv()
cfg = yaml.safe_load(open("config.yaml"))

llm = ChatNebius(
    model_name=cfg["chat"]["model"],
    temperature=cfg["generation"]["temperature"],
    max_tokens=cfg["generation"]["max_tokens"],
    nebius_api_base=cfg["nebius"]["api_base"],
    nebius_api_key=os.environ["NEBIUS_API_KEY"],
)

r = llm.invoke("Reply with the word OK")
print(r.content, r.response_metadata.get("finish_reason"))

from pydantic import BaseModel

class Answer(BaseModel):
    answer: str
    confident: bool

structured = llm.with_structured_output(Answer)
result = structured.invoke("Is the sky blue? Answer briefly.")
print(type(result).__name__, result)

# Pinecone: connection and namespace test (throwaway namespace, not the real one)
import time
from pinecone import Pinecone

pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
index = pc.Index(cfg["pinecone"]["index_name"])
dim = cfg["embedding"]["dimensions"]
vector = [0.1] * dim

index.upsert(vectors=[("test-1", vector)], namespace="smoke-test")
time.sleep(10)  # serverless indexes are eventually consistent

same = index.query(vector=vector, top_k=1, namespace="smoke-test")
other = index.query(vector=vector, top_k=1, namespace="other")
print("same namespace:", [m.id for m in same.matches])
print("other namespace:", [m.id for m in other.matches])

index.delete(delete_all=True, namespace="smoke-test")
