from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bid_knowledge.service.agent_material_context import AgentMaterialContextService, ProjectMaterialContextService
from bid_knowledge.service.result_browser import ResultBrowser, ResultNotFoundError


BASE_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Bid Result Browser")
browser = ResultBrowser(BASE_DIR / "outputs")
context_service = AgentMaterialContextService(BASE_DIR / "outputs")
project_context_service = ProjectMaterialContextService(BASE_DIR / "outputs", BASE_DIR / "configs" / "material_projects.json")


class MaterialContextRequest(BaseModel):
    section_path: str | None = None
    title: str | None = None
    top_k: int = 5


@app.get("/api/runs")
def list_runs() -> dict[str, object]:
    return {"runs": browser.list_runs()}


@app.get("/api/runs/{run_name}/modules/tree")
def get_module_tree(run_name: str) -> dict[str, object]:
    try:
        return browser.get_module_tree(run_name)
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_name}/materials/meta")
def get_material_meta(run_name: str, path: str = Query(...)) -> dict[str, object]:
    try:
        return browser.get_material_meta(run_name, path)
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_name}/materials/ordered")
def get_ordered_material(run_name: str, path: str = Query(...)) -> dict[str, object]:
    try:
        return browser.get_ordered_material(run_name, path)
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/runs/{run_name}/materials/context")
def get_material_context(run_name: str, request: MaterialContextRequest) -> dict[str, object]:
    try:
        return context_service.get_context(
            run_name,
            section_path=request.section_path,
            title=request.title,
            top_k=request.top_k,
        )
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/materials/context")
def get_project_material_context(project_id: str, request: MaterialContextRequest) -> dict[str, object]:
    try:
        return project_context_service.get_project_context(
            project_id,
            section_path=request.section_path,
            title=request.title,
            top_k=request.top_k,
        )
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_name}/items/detail")
def get_item_detail(
    run_name: str,
    material_path: str = Query(...),
    item_type: str = Query(...),
    item_name: str = Query(...),
) -> object:
    try:
        return browser.get_item_detail(run_name, material_path, item_type, item_name)
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_name}/images")
def get_image(run_name: str, path: str = Query(...)) -> FileResponse:
    try:
        file_path, media_type = browser.get_image_file(run_name, path)
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(file_path, media_type=media_type)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
