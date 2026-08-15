# GramCare AI — RoleBased v5 Telemedicine + CMO Edition

A Flask + SQLite hackathon prototype for rural connected care.

## Portals
- Patient: self registration, Health ID, PIN/OTP login, QR identity, timeline, medicines, reports, referrals, video consultations, admissions/discharge visibility.
- PHC Doctor: only patients belonging to the doctor's assigned PHC, clinical encounters, detailed AI decision support, doctor notes, prescriptions, follow-ups, referrals, telemedicine allocation, inpatient chart.
- CMO: district-wide PHC and doctor analytics, all-patient operational visibility, referral/admission/telemedicine totals, platform revenue, official notices targeted to a PHC.
- Worker login is intentionally removed.

## Video consultation workflow
Doctor opens a patient -> creates referral -> Referrals -> Allocate video consultation -> chooses consulting doctor, date/time, duration and doctor fee. Patient fee is automatically doctor fee + ₹100 platform fee. The patient portal only shows consultations allocated to that patient's Health ID. Both sides use the same Jitsi room.

## Demo credentials
- PHC doctor: asha.verma@gramcare.gov.in / doctor123
- PHC doctor 2: rajesh.kumar@gramcare.gov.in / doctor123
- CMO: cmo@gramcare.gov.in / cmo123
- Patient: +91 9876543210 / PIN 1234

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
python3 app.py
```
Open http://127.0.0.1:5000

## Gemini
Use a newly rotated Gemini key in `.env`. AI is decision support, not autonomous diagnosis. The prompt produces a detailed differential, possible causes, red flags, suggested tests and next steps while explicitly requiring clinician review.

## Notes
The video room uses Jitsi Meet for a working hackathon demo. A production medical deployment should use an appropriately secured/compliant telemedicine stack, authenticated room access, consent and applicable privacy/security controls.
