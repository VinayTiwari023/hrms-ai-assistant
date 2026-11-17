# NovaTech Innovations — Information Security Policy

> **DISCLAIMER:** This is an independently authored, fictional demonstration policy document for NovaTech Innovations. It is intended solely for AI assistant testing, evaluation, and educational demonstrations. It does not represent real legal advice, actual organizational policies, or real employee data.

---

- **Document Title:** Enterprise Information Security, Access Control, and Data Governance
- **Policy Identifier:** POL-SEC-2026-007
- **Effective Date:** January 1, 2026
- **Version:** 4.1
- **Policy Owner:** Chief Information Security Officer (CISO) & InfoSec Team
- **Target Access Level:** employee
- **Classification:** Confidential / Internal Policy

---

## 1. Purpose and Security Scope

This policy establishes mandatory operational security baselines to protect NovaTech Innovations intellectual property, cloud infrastructure, customer data, and employee privacy against unauthorized access, compromise, or exfiltration.

## 2. Authentication, Passwords, and Multi-Factor Authentication (MFA)

- **Mandatory MFA:** Multi-Factor Authentication via hardware security keys (FIDO2/WebAuthn) or time-based authenticator applications (TOTP) is mandatory across all single-sign-on (SSO), VPN, GitHub, AWS, and HRMS systems. SMS-based 2FA is prohibited.
- **Password Complexity:** Passphrases must be a minimum of sixteen (16) characters. Use of personal credentials or reuse across corporate and private services is strictly prohibited.
- **Password Managers:** All employees must utilize the enterprise-provided password manager (NovaVault) for credential generation and storage.

## 3. Workstation Security and Clean Desk Mandate

- **Screen Lock:** Workstations must automatically lock after five (5) minutes of inactivity. Manual locking (`Win + L` or `Cmd + Ctrl + Q`) is mandatory when stepping away from desks.
- **Clean Desk Policy:** Sensitive paperwork, sticky notes with passwords, and export media must be stored in locked drawers when unattended.
- **Removable Media:** Connecting unencrypted USB flash drives, external SSDs, or smartphones for data transfer to company machines is blocked by endpoint device control policies.

## 4. Data Classification and Sharing

- **Public:** Marketing collateral, public press releases, approved open-source libraries.
- **Internal:** Company announcements, general HR guidelines, employee handbooks.
- **Confidential:** Customer PII, commercial contract financials, infrastructure topology.
- **Restricted / Secret:** Production cryptographic keys, database credentials, M&A due diligence files. Restricted data must never be transmitted over unencrypted communication channels or pasted into public AI chatbots.

## 5. Security Incident Reporting and Escalation

- Any suspected phishing attempt, lost laptop, unauthorized access, or unusual credential alert must be reported immediately (within one hour) to the Security Operations Center (SOC).
- **Emergency SOC Hotline:** `+1 (800) 555-NOVA-SOC` (24/7/365)
- **SOC Email Alert:** `security-incidents@novatech-fictional.internal`
- **Slack Alert Channel:** `#infosec-incident-response`
