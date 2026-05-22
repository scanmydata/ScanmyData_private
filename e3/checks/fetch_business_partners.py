"""
Fetch Business Partners using Business Portal API
Fetches the names, VAT numbers, and roles of partners/owners/managers of a business
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
import requests

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
        self.api_key = os.getenv('BUSINESS_PORTAL_KEY')  # Updated to fetch from Infisical after bootstrap
        if not self.api_key:
            raise ValueError("Business Portal API key not configured (BUSINESS_PORTAL_KEY)")

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
            'error': None
        }

        try:
            # Prepare API request
            url = self.API_URL.format(arGemi=vat_number)
            headers = {
                'accept': 'application/json',
                'api_key': self.api_key
            }

            # Resolve AFM to ArGemi if needed and call the documented endpoint.
            ar_gemi = self._resolve_ar_gemi(vat_number)
            if not ar_gemi:
                raise ValueError('Could not resolve ArGemi from AFM')

            url = self.API_URL.format(arGemi=ar_gemi)
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            persons = self._extract_persons(data)

            if not persons:
                # Fallback: if no persons found from ArGemi endpoint, try search result persons.
                data = self._search_company_by_afm(vat_number)
                persons = self._extract_persons(data)

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
            result['error'] = 'Business Portal API timeout'
            log.error(f"Business Portal timeout for VAT {vat_number}")

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

    def _search_company_by_afm(self, vat_number: str) -> Dict[str, Any]:
        """Search company by AFM and return the first result body."""
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

        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        results = data.get('searchResults') or []
        return results[0] if results else {}

    def _resolve_ar_gemi(self, vat_number: str) -> str:
        """Resolve AFM to ArGemi using the search endpoint."""
        company = self._search_company_by_afm(vat_number)
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