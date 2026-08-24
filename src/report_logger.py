"""Service to log generated urban sprawl reports into reports_sent."""

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from src.database_config import get_session
from src.models.report_orm import ReportSent

logger = logging.getLogger(__name__)


class ReportLogger:
    """Logs urban sprawl reports to PostgreSQL."""

    @staticmethod
    def log_report(
        alert_type: str,
        report_title: str,
        report_url: str,
        report_date: date,
        gcs_bucket: str,
        gcs_prefix: str,
        year: int,
        month: int,
        top_upls: Optional[List[Dict[str, Any]]] = None,
        map_url: Optional[str] = None,
    ) -> Optional[str]:
        """Insert one generated report row in reports_sent."""
        session = None
        try:
            session = get_session()
            metadata = _build_metadata(
                gcs_bucket=gcs_bucket,
                gcs_prefix=gcs_prefix,
                year=year,
                month=month,
                top_upls=top_upls or [],
                map_url=map_url,
                report_url=report_url,
            )

            report = ReportSent(
                alert_type=alert_type,
                report_title=report_title,
                report_url=report_url,
                report_date=report_date,
                recipient_count=0,
                status="generated",
                metadata_json=metadata,
            )

            session.add(report)
            session.commit()

            report_id = str(report.id)
            logger.info(
                "Report logged to database: %s (%s) with %s files",
                report_id,
                report_title,
                len(metadata.get("files", [])),
            )
            return report_id
        except Exception as exc:
            if session:
                session.rollback()
            logger.error("Failed to log report to database: %s", exc, exc_info=True)
            return None
        finally:
            if session:
                session.close()


def _https_to_gs(url: str) -> str:
    """Convert storage.googleapis.com HTTPS URL to gs:// URL when possible."""
    prefix = "https://storage.googleapis.com/"
    if not url or not url.startswith(prefix):
        return url
    return "gs://" + url[len(prefix) :]


def _build_metadata(
    gcs_bucket: str,
    gcs_prefix: str,
    year: int,
    month: int,
    top_upls: List[Dict[str, Any]],
    map_url: Optional[str],
    report_url: str,
) -> Dict[str, Any]:
    """Build metadata payload consumed by simbyp-email-notifications."""
    folder_date = f"{year}_{month:02d}"
    base_gs = f"gs://{gcs_bucket}/{gcs_prefix}/{folder_date}"
    map_url_gs = _https_to_gs(map_url) if map_url else f"{base_gs}/maps/map_expansion_{year}_{month:02d}.html"

    files = [
        {
            "name": "Reporte mensual HTML",
            "url": f"{base_gs}/reportes/urban_sprawl_reporte_{year}_{month:02d}.html",
        },
        {
            "name": "Mapa interactivo",
            "url": f"{base_gs}/maps/map_expansion_{year}_{month:02d}.html",
        },
        {
            "name": "Estadisticas por UPL (CSV)",
            "url": f"{base_gs}/stats/resumen_expansion_upl_ha_{year}_{month:02d}.csv",
        },
        {
            "name": "Expansion urbana detectada (GeoJSON)",
            "url": f"{base_gs}/dw/new_urban_{year}_{month:02d}.geojson",
        },
        {
            "name": "Expansion urbana con restricciones (GeoJSON)",
            "url": f"{base_gs}/intersections/new_urban_{year}_{month:02d}_intersections.geojson",
        },
        {
            "name": "Expansion urbana sin restricciones (GeoJSON)",
            "url": f"{base_gs}/intersections/new_urban_{year}_{month:02d}_no_intersections.geojson",
        },
        {
            "name": "Raster de deteccion (TIF)",
            "url": f"{base_gs}/dw/new_urban_{year}_{month:02d}.tif",
        },
    ]

    return {
        "map_url": map_url_gs,
        "map_iframe_url": map_url_gs,
        "files": files,
        "top_upls": top_upls,
        "report_type": "monthly_built_area",
        "year": year,
        "month": month,
        "folder_date": folder_date,
        "report_url": _https_to_gs(report_url),
    }
