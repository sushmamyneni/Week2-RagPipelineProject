# Create the Pinecone serverless index from config.yaml (does nothing if it already exists)
import os
import time
import yaml
from dotenv import load_dotenv
from pinecone import Pinecone, ServerlessSpec

load_dotenv()
cfg = yaml.safe_load(open("config.yaml"))
pc_cfg = cfg["pinecone"]
name = pc_cfg["index_name"]
dimension = cfg["embedding"]["dimensions"]

pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])

if name in pc.list_indexes().names():
    d = pc.describe_index(name)
    print("exists:", name, d.dimension, d.metric)
else:
    pc.create_index(
        name=name,
        dimension=dimension,
        metric=pc_cfg["metric"],
        spec=ServerlessSpec(cloud=pc_cfg["cloud"], region=pc_cfg["region"]),
    )
    # Wait until the index is ready to accept vectors
    while not pc.describe_index(name).status["ready"]:
        time.sleep(2)
    d = pc.describe_index(name)
    print("created:", name, d.dimension, d.metric)
