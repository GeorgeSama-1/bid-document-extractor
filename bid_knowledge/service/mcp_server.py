from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from bid_knowledge.service.agent_material_context import AgentMaterialContextService, ProjectMaterialContextService
from bid_knowledge.service.result_browser import ResultNotFoundError


DEFAULT_OUTPUTS_DIR = Path(os.environ.get("BID_MATERIAL_OUTPUTS_DIR", Path.cwd() / "outputs"))
DEFAULT_PROJECTS_CONFIG = Path(os.environ.get("BID_MATERIAL_PROJECTS_CONFIG", Path.cwd() / "data" / "configs" / "material_projects.json"))


def get_material_context(
    run_name: str,
    section_path: str = "",
    title: str = "",
    top_k: int = 5,
    outputs_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return ready-to-use Markdown context for a bid material section."""
    service = AgentMaterialContextService(outputs_dir or DEFAULT_OUTPUTS_DIR)
    try:
        return service.get_context(
            run_name,
            section_path=section_path or None,
            title=title or None,
            top_k=top_k,
        )
    except ResultNotFoundError as exc:
        return {
            "status": "error",
            "message": str(exc),
            "candidates": [],
        }


def list_materials(
    run_name: str,
    limit: int = 200,
    outputs_dir: str | Path | None = None,
) -> dict[str, Any]:
    """List indexed materials for a parsed output run."""
    service = AgentMaterialContextService(outputs_dir or DEFAULT_OUTPUTS_DIR)
    try:
        records = service.build_index(run_name)
    except ResultNotFoundError as exc:
        return {
            "status": "error",
            "message": str(exc),
            "materials": [],
        }

    safe_limit = max(1, int(limit or 200))
    materials = [
        {
            "material_id": record.material_id,
            "section_path": record.section_path,
            "material_path": record.material_path,
            "title": record.title,
            "source_pages": record.source_pages,
        }
        for record in records[:safe_limit]
    ]
    return {
        "status": "ok",
        "count": len(records),
        "materials": materials,
    }


def get_project_material_context(
    project_id: str,
    section_path: str = "",
    title: str = "",
    top_k: int = 5,
    outputs_dir: str | Path | None = None,
    projects_config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return ready-to-use Markdown context across all runs in a bid project."""
    service = ProjectMaterialContextService(
        outputs_dir or DEFAULT_OUTPUTS_DIR,
        projects_config_path or DEFAULT_PROJECTS_CONFIG,
    )
    try:
        return service.get_project_context(
            project_id,
            section_path=section_path or None,
            title=title or None,
            top_k=top_k,
        )
    except ResultNotFoundError as exc:
        return {
            "status": "error",
            "project_id": project_id,
            "message": str(exc),
            "candidates": [],
        }


def create_mcp_server(
    outputs_dir: str | Path | None = None,
    projects_config_path: str | Path | None = None,
) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:
        raise RuntimeError("Missing MCP dependency. Install requirements.txt or run: pip install 'mcp>=1.2.0'") from exc

    resolved_outputs_dir = Path(outputs_dir or DEFAULT_OUTPUTS_DIR)
    resolved_projects_config = Path(projects_config_path or DEFAULT_PROJECTS_CONFIG)
    mcp = FastMCP("bid-material-context")

    @mcp.tool()
    def get_bid_material_context(
        run_name: str,
        section_path: str = "",
        title: str = "",
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Get reusable bid material Markdown by directory path or similar section title."""
        return get_material_context(
            run_name=run_name,
            section_path=section_path,
            title=title,
            top_k=top_k,
            outputs_dir=resolved_outputs_dir,
        )

    @mcp.tool()
    def list_bid_materials(run_name: str, limit: int = 200) -> dict[str, Any]:
        """List available materials in a parsed bid output run."""
        return list_materials(run_name=run_name, limit=limit, outputs_dir=resolved_outputs_dir)

    @mcp.tool()
    def get_bid_project_material_context(
        project_id: str,
        section_path: str = "",
        title: str = "",
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Get reusable bid material Markdown across business/technical runs in one bid project."""
        return get_project_material_context(
            project_id=project_id,
            section_path=section_path,
            title=title,
            top_k=top_k,
            outputs_dir=resolved_outputs_dir,
            projects_config_path=resolved_projects_config,
        )

    return mcp


def main() -> None:
    create_mcp_server().run()


if __name__ == "__main__":
    main()
