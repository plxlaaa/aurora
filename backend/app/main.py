"""FastAPI 网关入口（文档 10.1）。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import deps
from .routers.api import router

app = FastAPI(title="AI Assistant Gateway", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
async def on_startup() -> None:
    await deps.startup()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await deps.shutdown()