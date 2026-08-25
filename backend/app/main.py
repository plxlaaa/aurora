"""FastAPI 网关入口（文档 10.1）。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import deps
from .routers.api import router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await deps.startup()
    yield
    await deps.shutdown()


app = FastAPI(title="AI Assistant Gateway", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
