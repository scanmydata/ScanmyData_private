"""
Fetch Business Partners using Business Portal API
Fetches the names, VAT numbers, and roles of partners/owners/managers of a business
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Ensure root project directory is importable when running from e3/.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Load environment variables from .env first
load_dotenv(str(ROOT_DIR / '.env'))

# Load runtime secrets from Infisical using the same bootstrap logic as app.py
bootstrap_infisical_secrets = None
try:
    from infisical_bootstrap import bootstrap_infisical_secrets
    bootstrap_infisical_secrets(logger=logging.getLogger(__name__))
except Exception as _infisical_exc:
    logging.getLogger(__name__).warning("Infisical bootstrap unavailable: %s", _infisical_exc)

log = logging.getLogger(__name__)


def _first_non_empty(source: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    for key in keys:
        value = source.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _format_address(company: Dict[str, Any]) -> str:
    address_obj = company.get("address") if isinstance(company.get("address"), dict) else {}
    street = _first_non_empty(address_obj, ["street", "addressStreet", "streetName", "coStreet"])
    street_no = _first_non_empty(address_obj, ["streetNumber", "addressNumber", "streetNo", "coStreetNumber"])
    city = _first_non_empty(address_obj, ["city", "coCity", "addressCity"])
    zip_code = _first_non_empty(address_obj, ["zipCode", "postalCode", "zip", "coZipCode"])
    raw_address = _first_non_empty(company, ["address", "fullAddress", "companyAddress", "headquarterAddress", "registeredAddress"])
    if raw_address and isinstance(raw_address, str) and raw_address.strip():
        return raw_address.strip()
    parts = [str(x).strip() for x in [street, street_no, city, zip_code] if x and str(x).strip()]
    return " ".join(parts).strip()


_INDIVIDUAL_LEGAL_FORM_TOKENS = {
    "ΑΤΟΜΙΚΗ",
    "ΑΤΟΜΙΚΟ",
    "ΑΤΟΜΙΚΗΣ",
    "ΑΤΟΜΙΚΟΥ",
    "SOLE",
    "SOLE PROPRIETOR",
    "SOLE TRADER",
    "INDIVIDUAL",
    "ΜΟΝΟΠΡΟΣΩΠΗ",
    "ΑΤΟΜική",
}


def _is_individual_business(legal_form: Optional[str]) -> bool:
    if not legal_form:
        return False
    text = str(legal_form).upper()
    return any(token in text for token in _INDIVIDUAL_LEGAL_FORM_TOKENS)


def _normalize_company(company: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(company, dict):
        return {}

    normalized = dict(company)
    normalized["legalType"] = _first_non_empty(normalized, [
        "legalType",
        "legalTypeLabel",
        "coLegalType",
        "coLegalTypeLabel",
        "legalForm",
        "legalFormLabel",
        "legalFormName",
        "legalFormDescription",
        "companyLegalForm",
        "companyLegalType",
    ]) or normalized.get("legalType")
    normalized["legalForm"] = _first_non_empty(normalized, [
        "legalForm",
        "legalFormLabel",
        "legalFormName",
        "legalFormDescription",
        "companyLegalForm",
        "companyLegalType",
        "legalType",
        "legalTypeLabel",
    ]) or normalized.get("legalForm")

    address_obj = normalized.get("address") if isinstance(normalized.get("address"), dict) else {}
    normalized["street"] = _first_non_empty(normalized, [
        "street",
        "addressStreet",
        "streetName",
        "coStreet",
        "companyStreet",
    ]) or _first_non_empty(address_obj, [
        "street",
        "addressStreet",
        "streetName",
        "coStreet",
        "companyStreet",
    ])
    normalized["streetNumber"] = _first_non_empty(normalized, [
        "streetNumber",
        "addressNumber",
        "streetNo",
        "coStreetNumber",
        "companyStreetNumber",
    ]) or _first_non_empty(address_obj, [
        "streetNumber",
        "addressNumber",
        "streetNo",
        "coStreetNumber",
        "companyStreetNumber",
    ])
    normalized["zipCode"] = _first_non_empty(normalized, [
        "zipCode",
        "postalCode",
        "zip",
        "coZipCode",
        "companyZipCode",
    ]) or _first_non_empty(address_obj, [
        "zipCode",
        "postalCode",
        "zip",
        "coZipCode",
        "companyZipCode",
    ])
    normalized["city"] = _first_non_empty(normalized, [
        "city",
        "coCity",
        "addressCity",
        "companyCity",
    ]) or _first_non_empty(address_obj, [
        "city",
        "coCity",
        "addressCity",
        "companyCity",
    ])
    normalized["address"] = _format_address(normalized) or normalized.get("address")
    normalized["headquarter_address"] = normalized["address"]
    return normalized


class BusinessPortalFetcher:
    """Fetches business partners using Business Portal API"""

    API_URL = 'https://opendata-api.businessportal.gr/api/opendata/v1/companies/{arGemi}'
    SEARCH_URL = 'https://opendata-api.businessportal.gr/api/opendata/v1/companies'

    def __init__(self):
        if bootstrap_infisical_secrets is not None:
            try:
                bootstrap_infisical_secrets(logger=logging.getLogger(__name__))
            except Exception:
                logging.getLogger(__name__).warning("Infisical bootstrap unavailable. Secrets may not load correctly.")
        # Attempt to read Business Portal API key; do NOT raise here so caller can continue
        # even if the Business Portal is unavailable. Upstream logic should handle
        # a returned result with success==False and/or skipped_comparison==True.
        self.api_key = os.getenv('BUSINESS_PORTAL_KEY')  # Updated to fetch from Infisical after bootstrap
        if not self.api_key:
            logging.getLogger(__name__).warning("Business Portal API key not configured (BUSINESS_PORTAL_KEY); Business Portal checks will be skipped.")
            self.api_key = None

    def fetch_partners(self, vat_number: str) -> Dict[str, Any]:
        """
        Fetch the business partners for a given VAT number

        Args:
            vat_number: The VAT number of the business

        Returns:
            Dictionary with the result:
            {
                'success': bool,
                'partners': List[Dict[str, str]],
                'error': str or None
            }
        """
        result = {
            'success': False,
            'partners': [],
            'company': {},
            'error': None
        }

        try:
            # If API key missing, skip the external comparison but do not raise
            if not self.api_key:
                result['error'] = 'Business Portal API key not configured'
                result['skipped_comparison'] = True
                log.warning("Skipping Business Portal call for VAT %s: API key not configured", vat_number)
                return result

            # Prepare API request
            url = self.API_URL.format(arGemi=vat_number)
            headers = {
                'accept': 'application/json',
                'api_key': self.api_key
            }

            # Create a session with retries/backoff to handle transient timeouts
            session = requests.Session()
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET"]
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            session.mount("https://", adapter)

            # First try the search endpoint result (it often contains 'persons' already)
            search_result = self._search_company_by_afm(vat_number, session=session)
            if search_result and isinstance(search_result, dict) and search_result.get('persons'):
                data = search_result
            else:
                # Resolve AFM to ArGemi if needed and call the documented endpoint.
                ar_gemi = self._resolve_ar_gemi(vat_number, session=session)
                if not ar_gemi:
                    raise ValueError('Could not resolve ArGemi from AFM')

                url = self.API_URL.format(arGemi=ar_gemi)
                response = session.get(url, headers=headers, timeout=30)
                response.raise_for_status()
                data = response.json()
            result['company'] = self._extract_company(data)
            persons = self._extract_persons(data)

            if not persons:
                # Fallback: if no persons found from ArGemi endpoint, try search result persons.
                data = self._search_company_by_afm(vat_number)
                result['company'] = self._extract_company(data)
                persons = self._extract_persons(data)

            legal_form = _first_non_empty(result['company'], [
                'legalType', 'legalForm', 'legalTypeLabel', 'legalFormLabel',
                'legalFormName', 'legalTypeName', 'companyLegalForm', 'companyLegalType'
            ])
            if not persons and _is_individual_business(legal_form):
                result['success'] = True
                result['partners'] = []
                result['error'] = None
                return result

            if not persons:
                result['error'] = 'No persons found for the given VAT number'
                return result

            # Format persons
            for person in persons:
                person_info = {
                    'personName': person.get('personName'),
                    'businessName': person.get('businessName'),
                    'role': person.get('role'),
                    'category': person.get('category'),
                    'percentage': person.get('percentage'),
                    'dtFrom': person.get('dtFrom') or person.get('fromDate'),
                    'dtTo': person.get('dtTo') or person.get('toDate'),
                    'isRepresentativeAlone': person.get('isRepresentativeAlone'),
                    'isRepresentativeInCommon': person.get('isRepresentativeInCommon')
                }
                result['partners'].append(person_info)

            result['success'] = True

        except requests.exceptions.Timeout:
            # Do not abort the caller flow on timeout; mark that comparison was skipped
            result['error'] = 'Business Portal API timeout'
            result['skipped_comparison'] = True
            log.error("Business Portal timeout for VAT %s", vat_number)

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 'unknown'
            result['error'] = f'HTTP error {status_code}: {str(e)}'
            log.error(f"HTTP error for VAT {vat_number}: {status_code}")

        except requests.exceptions.RequestException as e:
            result['error'] = f'Network error: {str(e)}'
            log.error(f"Network error for VAT {vat_number}: {str(e)}")

        except Exception as e:
            result['error'] = f'Unexpected error: {str(e)}'
            log.exception(f"Unexpected error for VAT {vat_number}")

        return result

    def fetch_company_profile(self, vat_number: str) -> Dict[str, Any]:
        """Fetch company-level profile for a VAT number.

        Returns:
            {
                'success': bool,
                'company': Dict[str, Any],
                'error': str | None,
            }
        """
        result = {
            'success': False,
            'company': {},
            'error': None,
        }

        try:
            ar_gemi = self._resolve_ar_gemi(vat_number)
            if not ar_gemi:
                raise ValueError('Could not resolve ArGemi from AFM')

            headers = {
                'accept': 'application/json',
                'api_key': self.api_key
            }

            url = self.API_URL.format(arGemi=ar_gemi)
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()

            company = self._extract_company(data)
            if not company:
                data = self._search_company_by_afm(vat_number)
                company = self._extract_company(data)

            if not company:
                result['error'] = 'No company data found for the given VAT number'
                return result

            result['success'] = True
            result['company'] = company
            return result

        except requests.exceptions.Timeout:
            result['error'] = 'Business Portal API timeout'
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 'unknown'
            result['error'] = f'HTTP error {status_code}: {str(e)}'
        except requests.exceptions.RequestException as e:
            result['error'] = f'Network error: {str(e)}'
        except Exception as e:
            result['error'] = f'Unexpected error: {str(e)}'

        return result

    def _search_company_by_afm(self, vat_number: str) -> Dict[str, Any]:
        """Search company by AFM and return the first result body."""
        return self._search_company_by_afm(vat_number, session=None)

    def _search_company_by_afm(self, vat_number: str, session: Optional[requests.Session] = None) -> Dict[str, Any]:
        """Search company by AFM and return the first result body.

        If a `session` is provided it will be used for the HTTP request (and
        therefore will pick up any retry/backoff strategy attached to it).
        """
        url = self.SEARCH_URL
        headers = {
            'accept': 'application/json',
            'api_key': self.api_key
        }
        params = {
            'afm': str(vat_number).zfill(9),
            'resultsSortBy': '+arGemi',
            'resultsOffset': 0,
            'resultsSize': 10
        }

        getter = session.get if session is not None else requests.get
        response = getter(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        results = data.get('searchResults') or []
        return results[0] if results else {}

    def _resolve_ar_gemi(self, vat_number: str, session: Optional[requests.Session] = None) -> str:
        """Resolve AFM to ArGemi using the search endpoint.

        Accepts an optional `session` which will be used for the HTTP request
        (so retries/backoff can be applied).
        """
        company = self._search_company_by_afm(vat_number, session=session)
        return str(company.get('arGemi') or '').strip()

    @staticmethod
    def _extract_persons(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(data, dict):
            return []
        persons = data.get('persons') or []
        if not persons and isinstance(data.get('company'), dict):
            persons = data['company'].get('persons') or []
        if not persons:
            # Some endpoints return company data nested under searchResults
            search_results = data.get('searchResults') or []
            if search_results and isinstance(search_results, list):
                company = search_results[0] or {}
                persons = company.get('persons') or []
        return persons

    @staticmethod
    def _extract_company(data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {}

        if isinstance(data.get('company'), dict):
            return _normalize_company(data.get('company') or {})

        if data.get('coNameEl') or data.get('afm') or data.get('arGemi'):
            return _normalize_company(data)

        search_results = data.get('searchResults') or []
        if search_results and isinstance(search_results, list) and isinstance(search_results[0], dict):
            return _normalize_company(search_results[0])

        return {}

if __name__ == '__main__':
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python fetch_business_partners.py <VAT_NUMBER>")
        sys.exit(1)

    vat_number = sys.argv[1]
    fetcher = BusinessPortalFetcher()

    print(f"\nFetching business partners for VAT: {vat_number}")
    print("-" * 60)

    result = fetcher.fetch_partners(vat_number)

    if result['success']:
        persons_output = []
        for partner in result['partners']:
            persons_output.append({
                'personName': partner['personName'],
                'businessName': partner['businessName'],
                'role': partner['role'],
                'dtFrom': partner.get('dtFrom'),
                'dtTo': partner.get('dtTo'),
                'isRepresentativeAlone': partner['isRepresentativeAlone'],
                'isRepresentativeInCommon': partner['isRepresentativeInCommon'],
                'percentage': partner['percentage'],
                'category': partner['category']
            })

        output_file = Path.cwd() / f"business_persons_{vat_number}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({'persons': persons_output}, f, ensure_ascii=False, indent=2)

        print(f"Results written to: {output_file}")
        print("Partners:")
        for partner in result['partners']:
            name = partner['personName'] or partner['businessName']
            print(f"- Name: {name}, Role: {partner['role']}, Category: {partner['category']}, Percentage: {partner['percentage']}, Alone: {partner['isRepresentativeAlone']}, In Common: {partner['isRepresentativeInCommon']}")
    else:
        print(f"Error: {result['error']}")