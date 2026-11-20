"""One-click enterprise demo corpus: policy PDF, architecture diagram, meeting audio."""

from __future__ import annotations

import logging
import subprocess
import wave
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image, ImageDraw, ImageFont

from backend.config import Settings
from backend.services.pipeline import IngestionPipeline

logger = logging.getLogger("omnivault.demo")

POLICY_PAGES = [
    {
        "title": "HeliosPay — Information Security & Continuity Policy",
        "body": """
<div style="font-family: Helvetica, Arial, sans-serif; color: #0f172a;">
  <h1 style="font-size: 22px; margin: 0 0 8px 0;">HeliosPay Information Security Policy</h1>
  <p style="font-size: 11px; color:#334155; margin: 0 0 14px 0;">
    Document ID: POL-SEC-2026-04 &nbsp;|&nbsp; Owner: Chief Information Security Officer &nbsp;|&nbsp; Classification: Internal
  </p>
  <h2 style="font-size: 14px;">1. Purpose</h2>
  <p style="font-size: 11px; line-height: 1.45;">
    This policy defines how HeliosPay protects customer payment data and restores critical services after disruption.
    It implements controls aligned with KVKK and GDPR while keeping all production inference on-premise.
  </p>
  <h2 style="font-size: 14px;">2. Scope</h2>
  <p style="font-size: 11px; line-height: 1.45;">
    Applies to payment gateway, ledger, merchant portal, and disaster-recovery systems operated in Istanbul (primary)
    and Ankara (DR). Contractors processing cardholder data must follow the same recovery objectives.
  </p>
  <h2 style="font-size: 14px;">3. Recovery Objectives</h2>
  <table style="border-collapse: collapse; font-size: 10px; width: 100%;">
    <tr>
      <th style="border: 1px solid #334155; padding: 6px; background:#e2e8f0;">Tier</th>
      <th style="border: 1px solid #334155; padding: 6px; background:#e2e8f0;">System</th>
      <th style="border: 1px solid #334155; padding: 6px; background:#e2e8f0;">RTO</th>
      <th style="border: 1px solid #334155; padding: 6px; background:#e2e8f0;">RPO</th>
      <th style="border: 1px solid #334155; padding: 6px; background:#e2e8f0;">Owner</th>
    </tr>
    <tr>
      <td style="border: 1px solid #334155; padding: 6px;">Tier 1</td>
      <td style="border: 1px solid #334155; padding: 6px;">Payment Gateway</td>
      <td style="border: 1px solid #334155; padding: 6px;">4 hours</td>
      <td style="border: 1px solid #334155; padding: 6px;">15 minutes</td>
      <td style="border: 1px solid #334155; padding: 6px;">Platform SRE</td>
    </tr>
    <tr>
      <td style="border: 1px solid #334155; padding: 6px;">Tier 2</td>
      <td style="border: 1px solid #334155; padding: 6px;">Merchant Reporting</td>
      <td style="border: 1px solid #334155; padding: 6px;">24 hours</td>
      <td style="border: 1px solid #334155; padding: 6px;">4 hours</td>
      <td style="border: 1px solid #334155; padding: 6px;">Data Platform</td>
    </tr>
    <tr>
      <td style="border: 1px solid #334155; padding: 6px;">Tier 3</td>
      <td style="border: 1px solid #334155; padding: 6px;">Analytics Lakehouse</td>
      <td style="border: 1px solid #334155; padding: 6px;">72 hours</td>
      <td style="border: 1px solid #334155; padding: 6px;">24 hours</td>
      <td style="border: 1px solid #334155; padding: 6px;">Analytics</td>
    </tr>
  </table>
  <h2 style="font-size: 14px;">4. Data Residency</h2>
  <p style="font-size: 11px; line-height: 1.45;">
    Customer personal data and payment metadata must remain inside the Republic of Türkiye.
    Cloud LLM endpoints are prohibited for production knowledge retrieval. Contact the DPO at dpo@heliospay.example.
  </p>
</div>
""",
    },
    {
        "title": "Incident Response & Access Control",
        "body": """
<div style="font-family: Helvetica, Arial, sans-serif; color: #0f172a;">
  <h1 style="font-size: 20px;">Incident Response &amp; Access Control</h1>
  <h2 style="font-size: 14px;">5. Incident Severity</h2>
  <p style="font-size: 11px; line-height: 1.45;">
    Sev-1 payment outages page the on-call SRE within 5 minutes. Customer-visible authorization errors above 2%
    for 10 consecutive minutes escalate to Sev-1. The Ankara DR region is activated when Istanbul regional health
    stays below 90% for 15 minutes.
  </p>
  <h2 style="font-size: 14px;">6. Access Control</h2>
  <p style="font-size: 11px; line-height: 1.45;">
    Production Kubernetes access requires hardware security keys and break-glass approval from two engineers.
    Secrets are stored in the on-prem vault; plaintext credentials in tickets are a policy violation.
  </p>
  <h2 style="font-size: 14px;">7. Vendor AI Policy</h2>
  <p style="font-size: 11px; line-height: 1.45;">
    Employees must not paste cardholder data, IBAN numbers, or customer identifiers into public AI tools.
    OmniVault is the approved local retrieval environment for contracts, meeting recordings, and architecture diagrams.
  </p>
  <h2 style="font-size: 14px;">8. Review Cadence</h2>
  <p style="font-size: 11px; line-height: 1.45;">
    This policy is reviewed quarterly by Security Governance. Next review due: 2026-12-01.
    Related artifact: Payment Platform Architecture whiteboard (architecture-diagram.png).
  </p>
</div>
""",
    },
]

MEETING_SCRIPT = [
    (
        "Samantha",
        170,
        "Good morning everyone. This is Maya Chen, Head of Platform at HeliosPay. "
        "Today we are reviewing disaster recovery readiness for the payment gateway.",
    ),
    (
        "Daniel",
        165,
        "Thanks Maya. This is Omar Hassan from Site Reliability. "
        "Reminder: the Tier 1 recovery time objective for the payment gateway is four hours, "
        "and the recovery point objective is fifteen minutes.",
    ),
    (
        "Karen",
        170,
        "Confirmed. Primary Postgres stays in Istanbul. The Ankara replica receives asynchronous replication "
        "and is the designated disaster recovery region if Istanbul health drops below ninety percent for fifteen minutes.",
    ),
    (
        "Samantha",
        175,
        "Architecture note for the record: traffic enters through the API Gateway, then the Payment Service, "
        "then PostgreSQL Primary in Istanbul, with async replication to PostgreSQL Replica in Ankara D R.",
    ),
    (
        "Daniel",
        165,
        "Security reminder: customer personal data must remain in Türkiye under K V K K and G D P R. "
        "Do not upload production transcripts to cloud LLM providers. Use OmniVault on-premise instead.",
    ),
    (
        "Karen",
        170,
        "Action items: SRE will run a failover tabletop next Tuesday. "
        "Security will refresh the information security policy table for Tier 1 through Tier 3. Meeting adjourned.",
    ),
]


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates.extend(
            [
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/Library/Fonts/Arial Bold.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf",
            ]
        )
    candidates.extend(
        [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    )
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default(size=size)


def build_policy_pdf(path: Path) -> None:
    import pymupdf as fitz

    document = fitz.open()
    for page_spec in POLICY_PAGES:
        page = document.new_page(width=595, height=842)
        page.insert_htmlbox(fitz.Rect(48, 48, 547, 794), page_spec["body"])
    document.set_metadata(
        {
            "title": "HeliosPay Information Security Policy",
            "author": "HeliosPay CISO Office",
            "subject": "Security, continuity, KVKK/GDPR",
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()


def build_architecture_png(path: Path) -> None:
    width, height = 1400, 900
    image = Image.new("RGB", (width, height), "#F8FAFC")
    draw = ImageDraw.Draw(image)
    title = _font(36, bold=True)
    label = _font(22, bold=True)
    small = _font(18)
    draw.rectangle((0, 0, width, 88), fill="#0F172A")
    draw.text((40, 24), "HeliosPay — Payment Platform Architecture", fill="#F8FAFC", font=title)
    draw.text((40, 820), "Primary: Istanbul  ·  DR: Ankara  ·  Async replication  ·  Confidential", fill="#475569", font=small)

    boxes = [
        (90, 220, "API Gateway", "Edge TLS · rate limits"),
        (520, 220, "Payment Service", "AuthZ · ledger write"),
        (950, 180, "PostgreSQL Primary", "Istanbul · Tier 1"),
        (950, 520, "PostgreSQL Replica", "Ankara DR · RPO 15m"),
    ]
    for x, y, heading, sub in boxes:
        draw.rounded_rectangle((x, y, x + 340, y + 150), radius=22, outline="#1D4ED8", width=5, fill="#EFF6FF")
        draw.text((x + 24, y + 42), heading, fill="#0F172A", font=label)
        draw.text((x + 24, y + 84), sub, fill="#334155", font=small)

    # Arrows
    draw.line((430, 295, 520, 295), fill="#0F172A", width=5)
    draw.polygon([(520, 295), (505, 286), (505, 304)], fill="#0F172A")
    draw.line((860, 295, 950, 295), fill="#0F172A", width=5)
    draw.polygon([(950, 295), (935, 286), (935, 304)], fill="#0F172A")
    draw.line((1120, 330, 1120, 520), fill="#B91C1C", width=5)
    draw.polygon([(1120, 520), (1111, 505), (1129, 505)], fill="#B91C1C")
    draw.text((1135, 410), "async replication", fill="#B91C1C", font=small)
    draw.text((40, 120), "Whiteboard capture used in DR tabletop · OmniVault demo corpus", fill="#64748B", font=small)

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)


def _say_to_aiff(voice: str, rate: int, text: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["say", "-v", voice, "-r", str(rate), "-o", str(dest), text],
        check=True,
        capture_output=True,
    )


def _aiff_to_pcm(path: Path, sample_rate: int = 16000):
    import av
    import numpy as np

    frames = []
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                frames.append(resampled.to_ndarray())
        for resampled in resampler.resample(None):
            frames.append(resampled.to_ndarray())
    pcm = np.concatenate([frame.reshape(-1) for frame in frames]).astype("float32") / 32768.0
    return pcm


def build_meeting_wav(path: Path) -> None:
    import numpy as np

    from backend.ingest.audio_io import write_wav_mono16

    sample_rate = 16000
    pieces = []
    silence = np.zeros(int(sample_rate * 0.45), dtype=np.float32)
    scratch = path.parent / "_tts_parts"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        for index, (voice, rate, text) in enumerate(MEETING_SCRIPT):
            aiff = scratch / f"part_{index}.aiff"
            try:
                _say_to_aiff(voice, rate, text, aiff)
                pieces.append(_aiff_to_pcm(aiff, sample_rate))
            except Exception as exc:
                logger.warning("macOS say failed for voice=%s (%s); synthesizing tone placeholder", voice, exc)
                duration = max(2.5, min(12.0, len(text.split()) * 0.38))
                t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
                tone = 0.08 * np.sin(2 * np.pi * (220 + index * 40) * t)
                pieces.append(tone.astype(np.float32))
            pieces.append(silence.copy())
        audio = np.concatenate(pieces) if pieces else np.zeros(sample_rate, dtype=np.float32)
        write_wav_mono16(path, audio, sample_rate=sample_rate)
        # Persist a sidecar transcript for demos where Whisper is still downloading.
        transcript = path.with_suffix(".txt")
        transcript.write_text("\n\n".join(text for _, _, text in MEETING_SCRIPT), encoding="utf-8")
    finally:
        for leftover in scratch.glob("*"):
            try:
                leftover.unlink()
            except Exception:
                pass
        try:
            scratch.rmdir()
        except Exception:
            pass


def ensure_demo_assets(demo_dir: Path) -> Dict[str, Path]:
    demo_dir.mkdir(parents=True, exist_ok=True)
    assets = {
        "policy": demo_dir / "heliospay-security-policy.pdf",
        "diagram": demo_dir / "payment-architecture-diagram.png",
        "audio": demo_dir / "dr-readiness-meeting.wav",
    }
    if not assets["policy"].exists() or assets["policy"].stat().st_size < 1000:
        build_policy_pdf(assets["policy"])
    if not assets["diagram"].exists() or assets["diagram"].stat().st_size < 1000:
        build_architecture_png(assets["diagram"])
    if not assets["audio"].exists() or assets["audio"].stat().st_size < 1000:
        build_meeting_wav(assets["audio"])
    return assets


def seed_demo(settings: Settings, pipeline: IngestionPipeline, lang: str = "en") -> Dict[str, Any]:
    language = "tr" if str(lang).lower().startswith("tr") else "en"
    assets = ensure_demo_assets(settings.demo_dir)
    created: List[Dict[str, Any]] = []
    reused: List[Dict[str, Any]] = []
    plan = [
        ("heliospay-security-policy.pdf", assets["policy"]),
        ("payment-architecture-diagram.png", assets["diagram"]),
        ("dr-readiness-meeting.wav", assets["audio"]),
    ]
    for filename, path in plan:
        existing = pipeline.store.find_by_origin("demo", filename)
        if existing and existing.get("status") in {"ready", "queued", "processing", "indexing", "briefing"}:
            current_lang = str((existing.get("briefing") or {}).get("lang") or (existing.get("meta") or {}).get("lang") or "en")
            if existing.get("status") == "ready" and current_lang != language:
                refreshed = pipeline.rebrief(existing["id"], language)
                reused.append(refreshed or existing)
            else:
                reused.append(existing)
            continue
        document = pipeline.register_file(path, filename=filename, origin="demo", copy=True, lang=language)
        created.append(document)
    message = (
        f"Queued {len(created)} demo asset(s)"
        + (f"; reused {len(reused)} already indexed" if reused else "")
        + ". Watch the Knowledge Base while OmniVault extracts, embeds, and briefs each modality."
    )
    pipeline.store.audit("demo.seed", {"created": [d["id"] for d in created], "reused": [d["id"] for d in reused]})
    return {"created": created, "reused": reused, "message": message}
