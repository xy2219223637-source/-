from types import SimpleNamespace

from app.models import CreativeGeneration


def generation_summary_rows(query):
    """Keep historical prompts and full context snapshots out of read-only summaries."""
    job = CreativeGeneration
    rows = query.with_entities(
        job.id, job.project_id, job.action, job.provider, job.status, job.duration_ms,
        job.result_node_ids, job.created_at, job.updated_at,
        job.context_snapshot["diagnostic"].label("diagnostic"),
        job.context_snapshot["triggered_by"].label("triggered_by"),
        job.context_snapshot["trigger_source"].as_string().label("trigger_source"),
    ).all()
    return [SimpleNamespace(**{key: value for key, value in row._mapping.items()
                              if key not in {"diagnostic", "triggered_by", "trigger_source"}},
                            context_snapshot={key: value for key, value in {
                                "diagnostic": row.diagnostic, "triggered_by": row.triggered_by,
                                "trigger_source": row.trigger_source,
                            }.items() if value is not None}) for row in rows]
