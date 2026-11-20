"""Coordinator HTTP client and demo CLI."""

import argparse
import os
from uuid import uuid4

import httpx

from skyassist.config import CONFIG, Mode
from skyassist.models import (
    CaseState, DecisionKind, DecisionRequest, DecisionResponse, DispatchState,
    DispatchStatusRequest, DispatchStatusResponse, GetStaffCasesRequest,
    GetStaffCasesResponse, SourceStamp,
)
from skyassist.service import DEMO_COORDINATOR_TOKEN


class StaffClient:
    def __init__(self, base_url: str, token: str):
        self.http = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.close()

    def list_cases(self, request: GetStaffCasesRequest) -> GetStaffCasesResponse:
        params = request.model_dump(mode="json", exclude_none=True)
        response = self.http.get("/staff/cases", params=params)
        return GetStaffCasesResponse.model_validate_json(response.raise_for_status().content)

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        response = self.http.post(f"/staff/cases/{request.case_id}/decisions", json=request.model_dump(mode="json"))
        return DecisionResponse.model_validate_json(response.raise_for_status().content)

    def dispatch(self, request: DispatchStatusRequest) -> DispatchStatusResponse:
        response = self.http.post(f"/staff/cases/{request.case_id}/dispatch", json=request.model_dump(mode="json"))
        return DispatchStatusResponse.model_validate_json(response.raise_for_status().content)


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m skyassist.staff_client")
    parser.add_argument("command", choices=("list", "approve", "decline", "dispatch"))
    parser.add_argument("--case")
    parser.add_argument("--airport", default="DXB")
    parser.add_argument("--url", default=os.environ.get("SKYASSIST_SERVICE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--event-id")
    parser.add_argument("--revision", type=int)
    args = parser.parse_args()
    if args.command != "list" and not args.case:
        parser.error("--case is required")
    token = os.environ.get("SKYASSIST_COORDINATOR_TOKEN")
    if not token and CONFIG.mode == Mode.DEMO:
        token = DEMO_COORDINATOR_TOKEN
    if not token:
        parser.error("SKYASSIST_COORDINATOR_TOKEN is required")
    with StaffClient(args.url, token) as client:
        try:
            if args.command == "list":
                query = GetStaffCasesRequest(airport=args.airport, states=(
                    CaseState.PENDING_APPROVAL, CaseState.APPROVED,
                    CaseState.DISPATCH_PENDING_VERIFICATION, CaseState.DISPATCHED,
                ))
                print(client.list_cases(query).model_dump_json())
                return 0
            revision = args.revision
            if revision is None or args.command == "dispatch":
                query = GetStaffCasesRequest(airport=args.airport, states=tuple(CaseState))
                while True:
                    page = client.list_cases(query)
                    match = next((item.case for item in page.cases if item.case.id == args.case), None)
                    if match is not None:
                        if revision is None:
                            revision = match.request_revision
                        break
                    if page.next_cursor is None:
                        parser.error("case is outside the staff queue")
                    query = query.model_copy(update={"cursor": page.next_cursor})
            event_id = args.event_id or uuid4().hex
            if args.command == "dispatch":
                result = client.dispatch(DispatchStatusRequest(
                    event_id=event_id, case_id=args.case, request_revision=revision,
                    status=DispatchState.DISPATCHED, evidence=SourceStamp(source="demo-staff", observed_at=page.as_of),
                ))
            else:
                result = client.decide(DecisionRequest(
                    event_id=event_id, case_id=args.case, request_revision=revision,
                    decision=DecisionKind(args.command),
                ))
        except httpx.HTTPStatusError as exc:
            parser.exit(1, exc.response.text + "\n")
        except httpx.RequestError as exc:
            parser.exit(1, f"Staff service request failed ({type(exc).__name__})\n")
        print(result.model_dump_json())
        return int(result.reason_code is not None)


if __name__ == "__main__":
    raise SystemExit(main())
