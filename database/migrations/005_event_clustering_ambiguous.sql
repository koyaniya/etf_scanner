-- Canonical Event Clustering Stage 3: preserve ambiguous deterministic
-- decisions for later LLM comparison without forcing an incorrect event link.

begin;

alter table public.event_clustering_runs
    drop constraint if exists event_clustering_runs_decision_check,
    add constraint event_clustering_runs_decision_check
        check (decision is null or decision in ('NEW_EVENT', 'MATCHED', 'AMBIGUOUS')),
    drop constraint if exists event_clustering_runs_success_check,
    add constraint event_clustering_runs_success_check
        check (
            status <> 'SUCCESS'
            or (
                decision is not null
                and decision_method is not null
                and (
                    (decision = 'AMBIGUOUS' and selected_event_id is null)
                    or (decision <> 'AMBIGUOUS' and selected_event_id is not null)
                )
                and error_message is null
                and completed_at is not null
            )
        );

commit;
