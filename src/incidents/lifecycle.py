"""Deterministic incident lifecycle for RESON incident intelligence.

Lifecycle policy (offline, batch analysis)
------------------------------------------
- a newly detected incident is ``OPEN`` (end not yet established);
- an explicitly requested investigation state is ``INVESTIGATING``;
- evidence of recovery after the last supporting signal sets ``RESOLVED`` with
  the end time fixed at the latest supporting evidence.

Recovery is never inferred from silence. It requires a *positive* signal:

- a successful deployment for an affected service strictly after the last
  supporting evidence timestamp, or
- a rolled-back deployment for an affected service at or after the last
  supporting evidence timestamp (the rollback is itself the mitigation
  milestone, so it closes the incident even when it is the final evidence),
  or
- a deviation with status ``NORMAL`` for an affected metric key strictly after
  that key's last anomalous deviation (the metric demonstrably returned to
  baseline).

If neither recovery nor an investigation request applies, the incident stays
``OPEN`` and ``end_time`` stays ``None``; the last known state is preserved
rather than inventing a transition the telemetry cannot support.

Transitions
-----------
``OPEN -> INVESTIGATING -> RESOLVED`` and ``OPEN -> RESOLVED`` are the only
allowed forward transitions. Anything else raises ``ValueError``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Sequence

from src.data.schemas import (
    Deployment,
    DeploymentStatus,
    Incident,
    IncidentStatus,
)
from src.intelligence.models import BaselineKey, MetricDeviation
from src.incidents.models import Signal

RECOVERY_DEPLOYMENT_STATUSES = (
    DeploymentStatus.SUCCESS,
    DeploymentStatus.ROLLED_BACK,
)

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    IncidentStatus.OPEN.value: {
        IncidentStatus.INVESTIGATING.value,
        IncidentStatus.RESOLVED.value,
    },
    IncidentStatus.INVESTIGATING.value: {
        IncidentStatus.RESOLVED.value,
    },
}


def _key_string(key: BaselineKey) -> str:
    return f"{key.service}/{key.environment}/{key.metric_name}"


class IncidentLifecycle:
    """Manage deterministic incident status transitions."""

    def finalize(
        self,
        incident: Incident,
        *,
        signals: Sequence[Signal],
        deviations: Iterable[MetricDeviation] = (),
        deployments: Iterable[Deployment] = (),
        investigate: bool = False,
    ) -> Incident:
        """Resolve the final status for a freshly detected incident."""
        if investigate:
            return self.transition(incident, IncidentStatus.INVESTIGATING)

        recovery = self._has_recovery(incident, signals, deviations, deployments)
        if recovery:
            return self.transition(incident, IncidentStatus.RESOLVED)

        return incident.model_copy(update={"end_time": None})

    def transition(
        self,
        incident: Incident,
        to: IncidentStatus,
    ) -> Incident:
        """Move an incident to a documented state.

        ``RESOLVED`` requires an established end time (the latest supporting
        evidence); ``OPEN``/``INVESTIGATING`` incidents carry ``end_time=None``.
        """
        target = to.value if hasattr(to, "value") else str(to)

        if target == incident.status:
            return incident

        allowed = _ALLOWED_TRANSITIONS.get(incident.status, set())
        if target not in allowed:
            raise ValueError(
                f"Transition {incident.status} -> {target} is not allowed."
            )

        if target in (
            IncidentStatus.OPEN.value,
            IncidentStatus.INVESTIGATING.value,
        ):
            return incident.model_copy(
                update={"status": target, "end_time": None}
            )

        return incident.model_copy(update={"status": target})

    def investigate(self, incident: Incident) -> Incident:
        """Explicitly open an investigation on an open incident."""
        return self.transition(incident, IncidentStatus.INVESTIGATING)

    def resolve(self, incident: Incident) -> Incident:
        """Mark an open/investigating incident resolved."""
        return self.transition(incident, IncidentStatus.RESOLVED)

    def _has_recovery(
        self,
        incident: Incident,
        signals: Sequence[Signal],
        deviations: Iterable[MetricDeviation],
        deployments: Iterable[Deployment],
    ) -> bool:
        if incident.end_time is None:
            return False

        end = incident.end_time
        affected = set(incident.affected_services)

        for deployment in deployments:
            if deployment.service not in affected:
                continue
            if deployment.status not in RECOVERY_DEPLOYMENT_STATUSES:
                continue
            if deployment.status == DeploymentStatus.ROLLED_BACK:
                # A rollback is the mitigation milestone: at-or-after the last
                # supporting evidence it closes the incident.
                if deployment.timestamp >= end:
                    return True
            elif deployment.timestamp > end:
                # A plain success only counts when it genuinely lands after
                # the evidence span; inside the incident it is context, not
                # recovery.
                return True

        metric_signals = [
            signal for signal in signals if signal.related_keys
        ]
        if not metric_signals:
            return False

        deviation_list = list(deviations)
        last_anomalous: dict[str, datetime | None] = {
            _key_string(key): None
            for signal in metric_signals
            for key in signal.related_keys
        }

        for deviation in deviation_list:
            label = _key_string(deviation.key)
            if label not in last_anomalous:
                continue
            if (
                deviation.is_anomalous
                and (
                    last_anomalous[label] is None
                    or deviation.timestamp > last_anomalous[label]
                )
            ):
                last_anomalous[label] = deviation.timestamp

        for deviation in deviation_list:
            label = _key_string(deviation.key)
            if label not in last_anomalous:
                continue
            last = last_anomalous[label]
            if last is None:
                continue
            if deviation.status.value == "normal" and deviation.timestamp > last:
                return True

        return False