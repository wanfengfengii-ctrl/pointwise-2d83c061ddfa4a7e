from intake.receipts import ReceiptStore
from intake.verify_envelope import verify_envelope
from intake.verify_evidence import verify_evidence
from intake.verify_interval import verify_interval_evidence
from issue.envelope import V1, V2
from issue.envelope import IssueRegistration


def receive_packet(store: ReceiptStore, packet: bytes, registration: IssueRegistration) -> int:
    envelope = verify_envelope(packet, registration)
    if envelope.version == V1:
        evidence = verify_evidence(envelope)
    elif envelope.version == V2:
        evidence = verify_interval_evidence(envelope)
    else:  # pragma: no cover - the envelope verifier rejects other versions
        raise ValueError('unsupported packet version')
    return store.record_receipt(envelope, evidence)
