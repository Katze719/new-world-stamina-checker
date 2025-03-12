from pydrive2.auth import GoogleAuth
import gspread_asyncio

def _login_with_service_account() -> GoogleAuth:
    """
    Google Drive service with a service account.
    note: for the service account to work, you need to share the folder or
    files with the service account email.

    :return: google auth
    """
    # Define the settings dict to use a service account
    # We also can use all options available for the settings dict like
    # oauth_scope,save_credentials,etc.
    settings = {
                "client_config_backend": "service",
                "service_config": {
                    "client_json_file_path": "black-beach-453214-f6-3393b23dd7f1.json",
                }
            }
    # Create instance of GoogleAuth
    gauth = GoogleAuth(settings=settings)
    # Authenticate
    gauth.ServiceAuth()
    return gauth

def create_gspread_manager():
    gauth = _login_with_service_account()
    def get_creds():
        return gauth.credentials
    return gspread_asyncio.AsyncioGspreadClientManager(get_creds)

