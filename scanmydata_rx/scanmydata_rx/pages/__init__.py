"""Pages package — imports all page functions so they register routes."""
from .account import account_page
from .admin import (
    admin_dashboard_page,
    admin_group_detail_page,
    admin_groups_page,
    admin_settings_page,
    admin_user_detail_page,
    admin_users_page,
)
from .credentials import credentials_page
from .credentials_edit import credentials_edit_page
from .custom_categories import custom_categories_page
from .epsilon_preview import epsilon_preview_page
from .fetch import fetch_page
from .forgot_password import forgot_password_page
from .groups import groups_page
from .home import home_page
from .login import login_page
from .options import options_page
from .privacy import privacy_page
from .profile import profile_page
from .profiles import profiles_page
from .search import search_page
from .signup import signup_page
from .terms import terms_page

__all__ = [
    "home_page",
    "login_page",
    "signup_page",
    "forgot_password_page",
    "profile_page",
    "fetch_page",
    "credentials_page",
    "credentials_edit_page",
    "search_page",
    "profiles_page",
    "custom_categories_page",
    "options_page",
    "epsilon_preview_page",
    "terms_page",
    "privacy_page",
    "groups_page",
    "account_page",
    "admin_dashboard_page",
    "admin_users_page",
    "admin_user_detail_page",
    "admin_groups_page",
    "admin_group_detail_page",
    "admin_settings_page",
]
