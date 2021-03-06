# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Integration tests for firebase_admin.auth module."""
import base64
import datetime
import random
import time
from urllib import parse
import uuid

import google.oauth2.credentials
from google.auth import transport
import pytest
import requests

import firebase_admin
from firebase_admin import auth
from firebase_admin import credentials


_verify_token_url = 'https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyCustomToken'
_verify_password_url = 'https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyPassword'
_password_reset_url = 'https://www.googleapis.com/identitytoolkit/v3/relyingparty/resetPassword'
_verify_email_url = 'https://www.googleapis.com/identitytoolkit/v3/relyingparty/setAccountInfo'
_email_sign_in_url = 'https://www.googleapis.com/identitytoolkit/v3/relyingparty/emailLinkSignin'

ACTION_LINK_CONTINUE_URL = 'http://localhost?a=1&b=5#f=1'

def _sign_in(custom_token, api_key):
    body = {'token' : custom_token.decode(), 'returnSecureToken' : True}
    params = {'key' : api_key}
    resp = requests.request('post', _verify_token_url, params=params, json=body)
    resp.raise_for_status()
    return resp.json().get('idToken')

def _sign_in_with_password(email, password, api_key):
    body = {'email': email, 'password': password}
    params = {'key' : api_key}
    resp = requests.request('post', _verify_password_url, params=params, json=body)
    resp.raise_for_status()
    return resp.json().get('idToken')

def _random_id():
    random_id = str(uuid.uuid4()).lower().replace('-', '')
    email = 'test{0}@example.{1}.com'.format(random_id[:12], random_id[12:])
    return random_id, email

def _random_phone():
    return '+1' + ''.join([str(random.randint(0, 9)) for _ in range(0, 10)])

def _reset_password(oob_code, new_password, api_key):
    body = {'oobCode': oob_code, 'newPassword': new_password}
    params = {'key' : api_key}
    resp = requests.request('post', _password_reset_url, params=params, json=body)
    resp.raise_for_status()
    return resp.json().get('email')

def _verify_email(oob_code, api_key):
    body = {'oobCode': oob_code}
    params = {'key' : api_key}
    resp = requests.request('post', _verify_email_url, params=params, json=body)
    resp.raise_for_status()
    return resp.json().get('email')

def _sign_in_with_email_link(email, oob_code, api_key):
    body = {'oobCode': oob_code, 'email': email}
    params = {'key' : api_key}
    resp = requests.request('post', _email_sign_in_url, params=params, json=body)
    resp.raise_for_status()
    return resp.json().get('idToken')

def _extract_link_params(link):
    query = parse.urlparse(link).query
    query_dict = dict(parse.parse_qsl(query))
    return query_dict

def test_custom_token(api_key):
    custom_token = auth.create_custom_token('user1')
    id_token = _sign_in(custom_token, api_key)
    claims = auth.verify_id_token(id_token)
    assert claims['uid'] == 'user1'

def test_custom_token_without_service_account(api_key):
    google_cred = firebase_admin.get_app().credential.get_credential()
    cred = CredentialWrapper.from_existing_credential(google_cred)
    custom_app = firebase_admin.initialize_app(cred, {
        'serviceAccountId': google_cred.service_account_email,
        'projectId': firebase_admin.get_app().project_id
    }, 'temp-app')
    try:
        custom_token = auth.create_custom_token('user1', app=custom_app)
        id_token = _sign_in(custom_token, api_key)
        claims = auth.verify_id_token(id_token)
        assert claims['uid'] == 'user1'
    finally:
        firebase_admin.delete_app(custom_app)

def test_custom_token_with_claims(api_key):
    dev_claims = {'premium' : True, 'subscription' : 'silver'}
    custom_token = auth.create_custom_token('user2', dev_claims)
    id_token = _sign_in(custom_token, api_key)
    claims = auth.verify_id_token(id_token)
    assert claims['uid'] == 'user2'
    assert claims['premium'] is True
    assert claims['subscription'] == 'silver'

def test_session_cookies(api_key):
    dev_claims = {'premium' : True, 'subscription' : 'silver'}
    custom_token = auth.create_custom_token('user3', dev_claims)
    id_token = _sign_in(custom_token, api_key)
    expires_in = datetime.timedelta(days=1)
    session_cookie = auth.create_session_cookie(id_token, expires_in=expires_in)
    claims = auth.verify_session_cookie(session_cookie)
    assert claims['uid'] == 'user3'
    assert claims['premium'] is True
    assert claims['subscription'] == 'silver'
    assert claims['iss'].startswith('https://session.firebase.google.com')
    estimated_exp = int(time.time() + expires_in.total_seconds())
    assert abs(claims['exp'] - estimated_exp) < 5

def test_session_cookie_error():
    expires_in = datetime.timedelta(days=1)
    with pytest.raises(auth.InvalidIdTokenError):
        auth.create_session_cookie('not.a.token', expires_in=expires_in)

def test_get_non_existing_user():
    with pytest.raises(auth.UserNotFoundError) as excinfo:
        auth.get_user('non.existing')
    assert str(excinfo.value) == 'No user record found for the provided user ID: non.existing.'

def test_get_non_existing_user_by_email():
    with pytest.raises(auth.UserNotFoundError) as excinfo:
        auth.get_user_by_email('non.existing@definitely.non.existing')
    error_msg = ('No user record found for the provided email: '
                 'non.existing@definitely.non.existing.')
    assert str(excinfo.value) == error_msg

def test_update_non_existing_user():
    with pytest.raises(auth.UserNotFoundError):
        auth.update_user('non.existing')

def test_delete_non_existing_user():
    with pytest.raises(auth.UserNotFoundError):
        auth.delete_user('non.existing')

@pytest.fixture
def new_user():
    user = auth.create_user()
    yield user
    auth.delete_user(user.uid)

@pytest.fixture
def new_user_with_params():
    random_id, email = _random_id()
    phone = _random_phone()
    user = auth.create_user(
        uid=random_id,
        email=email,
        phone_number=phone,
        display_name='Random User',
        photo_url='https://example.com/photo.png',
        email_verified=True,
        password='secret',
    )
    yield user
    auth.delete_user(user.uid)

@pytest.fixture
def new_user_list():
    users = [
        auth.create_user(password='password').uid,
        auth.create_user(password='password').uid,
        auth.create_user(password='password').uid,
    ]
    yield users
    for uid in users:
        auth.delete_user(uid)

@pytest.fixture
def new_user_email_unverified():
    random_id, email = _random_id()
    user = auth.create_user(
        uid=random_id,
        email=email,
        email_verified=False,
        password='password'
    )
    yield user
    auth.delete_user(user.uid)

def test_get_user(new_user_with_params):
    user = auth.get_user(new_user_with_params.uid)
    assert user.uid == new_user_with_params.uid
    assert user.display_name == 'Random User'
    assert user.email == new_user_with_params.email
    assert user.phone_number == new_user_with_params.phone_number
    assert user.photo_url == 'https://example.com/photo.png'
    assert user.email_verified is True
    assert user.disabled is False

    user = auth.get_user_by_email(new_user_with_params.email)
    assert user.uid == new_user_with_params.uid
    user = auth.get_user_by_phone_number(new_user_with_params.phone_number)
    assert user.uid == new_user_with_params.uid

    assert len(user.provider_data) == 2
    provider_ids = sorted([provider.provider_id for provider in user.provider_data])
    assert provider_ids == ['password', 'phone']

def test_list_users(new_user_list):
    err_msg_template = (
        'Missing {field} field. A common cause would be forgetting to add the "Firebase ' +
        'Authentication Admin" permission. See instructions in CONTRIBUTING.md')

    fetched = []
    # Test exporting all user accounts.
    page = auth.list_users()
    while page:
        for user in page.users:
            assert isinstance(user, auth.ExportedUserRecord)
            if user.uid in new_user_list:
                fetched.append(user.uid)
                assert user.password_hash is not None, (
                    err_msg_template.format(field='password_hash'))
                assert user.password_salt is not None, (
                    err_msg_template.format(field='password_salt'))
        page = page.get_next_page()
    assert len(fetched) == len(new_user_list)

    fetched = []
    page = auth.list_users()
    for user in page.iterate_all():
        assert isinstance(user, auth.ExportedUserRecord)
        if user.uid in new_user_list:
            fetched.append(user.uid)
            assert user.password_hash is not None, (
                err_msg_template.format(field='password_hash'))
            assert user.password_salt is not None, (
                err_msg_template.format(field='password_salt'))
    assert len(fetched) == len(new_user_list)

def test_create_user(new_user):
    user = auth.get_user(new_user.uid)
    assert user.uid == new_user.uid
    assert user.display_name is None
    assert user.email is None
    assert user.phone_number is None
    assert user.photo_url is None
    assert user.email_verified is False
    assert user.disabled is False
    assert user.custom_claims is None
    assert user.user_metadata.creation_timestamp > 0
    assert user.user_metadata.last_sign_in_timestamp is None
    assert len(user.provider_data) == 0
    with pytest.raises(auth.UidAlreadyExistsError):
        auth.create_user(uid=new_user.uid)

def test_update_user(new_user):
    _, email = _random_id()
    phone = _random_phone()
    user = auth.update_user(
        new_user.uid,
        email=email,
        phone_number=phone,
        display_name='Updated Name',
        photo_url='https://example.com/photo.png',
        email_verified=True,
        password='secret')
    assert user.uid == new_user.uid
    assert user.display_name == 'Updated Name'
    assert user.email == email
    assert user.phone_number == phone
    assert user.photo_url == 'https://example.com/photo.png'
    assert user.email_verified is True
    assert user.disabled is False
    assert user.custom_claims is None
    assert len(user.provider_data) == 2

def test_set_custom_user_claims(new_user, api_key):
    claims = {'admin' : True, 'package' : 'gold'}
    auth.set_custom_user_claims(new_user.uid, claims)
    user = auth.get_user(new_user.uid)
    assert user.custom_claims == claims
    custom_token = auth.create_custom_token(new_user.uid)
    id_token = _sign_in(custom_token, api_key)
    dev_claims = auth.verify_id_token(id_token)
    for key, value in claims.items():
        assert dev_claims[key] == value

def test_update_custom_user_claims(new_user):
    assert new_user.custom_claims is None
    claims = {'admin' : True, 'package' : 'gold'}
    auth.set_custom_user_claims(new_user.uid, claims)
    user = auth.get_user(new_user.uid)
    assert user.custom_claims == claims

    claims = {'admin' : False, 'subscription' : 'guest'}
    auth.set_custom_user_claims(new_user.uid, claims)
    user = auth.get_user(new_user.uid)
    assert user.custom_claims == claims

    auth.set_custom_user_claims(new_user.uid, None)
    user = auth.get_user(new_user.uid)
    assert user.custom_claims is None

def test_disable_user(new_user_with_params):
    user = auth.update_user(
        new_user_with_params.uid,
        display_name=auth.DELETE_ATTRIBUTE,
        photo_url=auth.DELETE_ATTRIBUTE,
        phone_number=auth.DELETE_ATTRIBUTE,
        disabled=True)
    assert user.uid == new_user_with_params.uid
    assert user.email == new_user_with_params.email
    assert user.display_name is None
    assert user.phone_number is None
    assert user.photo_url is None
    assert user.email_verified is True
    assert user.disabled is True
    assert len(user.provider_data) == 1

def test_delete_user():
    user = auth.create_user()
    auth.delete_user(user.uid)
    with pytest.raises(auth.UserNotFoundError):
        auth.get_user(user.uid)

def test_revoke_refresh_tokens(new_user):
    user = auth.get_user(new_user.uid)
    old_valid_after = user.tokens_valid_after_timestamp
    time.sleep(1)
    auth.revoke_refresh_tokens(new_user.uid)
    user = auth.get_user(new_user.uid)
    new_valid_after = user.tokens_valid_after_timestamp
    assert new_valid_after > old_valid_after

def test_verify_id_token_revoked(new_user, api_key):
    custom_token = auth.create_custom_token(new_user.uid)
    id_token = _sign_in(custom_token, api_key)
    claims = auth.verify_id_token(id_token)
    assert claims['iat'] * 1000 >= new_user.tokens_valid_after_timestamp

    time.sleep(1)
    auth.revoke_refresh_tokens(new_user.uid)
    claims = auth.verify_id_token(id_token, check_revoked=False)
    user = auth.get_user(new_user.uid)
    # verify_id_token succeeded because it didn't check revoked.
    assert claims['iat'] * 1000 < user.tokens_valid_after_timestamp

    with pytest.raises(auth.RevokedIdTokenError) as excinfo:
        claims = auth.verify_id_token(id_token, check_revoked=True)
    assert str(excinfo.value) == 'The Firebase ID token has been revoked.'

    # Sign in again, verify works.
    id_token = _sign_in(custom_token, api_key)
    claims = auth.verify_id_token(id_token, check_revoked=True)
    assert claims['iat'] * 1000 >= user.tokens_valid_after_timestamp

def test_verify_session_cookie_revoked(new_user, api_key):
    custom_token = auth.create_custom_token(new_user.uid)
    id_token = _sign_in(custom_token, api_key)
    session_cookie = auth.create_session_cookie(id_token, expires_in=datetime.timedelta(days=1))

    time.sleep(1)
    auth.revoke_refresh_tokens(new_user.uid)
    claims = auth.verify_session_cookie(session_cookie, check_revoked=False)
    user = auth.get_user(new_user.uid)
    # verify_session_cookie succeeded because it didn't check revoked.
    assert claims['iat'] * 1000 < user.tokens_valid_after_timestamp

    with pytest.raises(auth.RevokedSessionCookieError) as excinfo:
        claims = auth.verify_session_cookie(session_cookie, check_revoked=True)
    assert str(excinfo.value) == 'The Firebase session cookie has been revoked.'

    # Sign in again, verify works.
    id_token = _sign_in(custom_token, api_key)
    session_cookie = auth.create_session_cookie(id_token, expires_in=datetime.timedelta(days=1))
    claims = auth.verify_session_cookie(session_cookie, check_revoked=True)
    assert claims['iat'] * 1000 >= user.tokens_valid_after_timestamp

def test_import_users():
    uid, email = _random_id()
    user = auth.ImportUserRecord(uid=uid, email=email)
    result = auth.import_users([user])
    try:
        assert result.success_count == 1
        assert result.failure_count == 0
        saved_user = auth.get_user(uid)
        assert saved_user.email == email
    finally:
        auth.delete_user(uid)

def test_import_users_with_password(api_key):
    uid, email = _random_id()
    password_hash = base64.b64decode(
        'V358E8LdWJXAO7muq0CufVpEOXaj8aFiC7T/rcaGieN04q/ZPJ08WhJEHGjj9lz/2TT+/86N5VjVoc5DdBhBiw==')
    user = auth.ImportUserRecord(
        uid=uid, email=email, password_hash=password_hash, password_salt=b'NaCl')

    scrypt_key = base64.b64decode(
        'jxspr8Ki0RYycVU8zykbdLGjFQ3McFUH0uiiTvC8pVMXAn210wjLNmdZJzxUECKbm0QsEmYUSDzZvpjeJ9WmXA==')
    salt_separator = base64.b64decode('Bw==')
    scrypt = auth.UserImportHash.scrypt(
        key=scrypt_key, salt_separator=salt_separator, rounds=8, memory_cost=14)
    result = auth.import_users([user], hash_alg=scrypt)
    try:
        assert result.success_count == 1
        assert result.failure_count == 0
        saved_user = auth.get_user(uid)
        assert saved_user.email == email
        id_token = _sign_in_with_password(email, 'password', api_key)
        assert len(id_token) > 0
    finally:
        auth.delete_user(uid)

def test_password_reset(new_user_email_unverified, api_key):
    link = auth.generate_password_reset_link(new_user_email_unverified.email)
    assert isinstance(link, str)
    query_dict = _extract_link_params(link)
    user_email = _reset_password(query_dict['oobCode'], 'newPassword', api_key)
    assert new_user_email_unverified.email == user_email
    # password reset also set email_verified to True
    assert auth.get_user(new_user_email_unverified.uid).email_verified

def test_email_verification(new_user_email_unverified, api_key):
    link = auth.generate_email_verification_link(new_user_email_unverified.email)
    assert isinstance(link, str)
    query_dict = _extract_link_params(link)
    user_email = _verify_email(query_dict['oobCode'], api_key)
    assert new_user_email_unverified.email == user_email
    assert auth.get_user(new_user_email_unverified.uid).email_verified

def test_password_reset_with_settings(new_user_email_unverified, api_key):
    action_code_settings = auth.ActionCodeSettings(ACTION_LINK_CONTINUE_URL)
    link = auth.generate_password_reset_link(new_user_email_unverified.email,
                                             action_code_settings=action_code_settings)
    assert isinstance(link, str)
    query_dict = _extract_link_params(link)
    assert query_dict['continueUrl'] == ACTION_LINK_CONTINUE_URL
    user_email = _reset_password(query_dict['oobCode'], 'newPassword', api_key)
    assert new_user_email_unverified.email == user_email
    # password reset also set email_verified to True
    assert auth.get_user(new_user_email_unverified.uid).email_verified

def test_email_verification_with_settings(new_user_email_unverified, api_key):
    action_code_settings = auth.ActionCodeSettings(ACTION_LINK_CONTINUE_URL)
    link = auth.generate_email_verification_link(new_user_email_unverified.email,
                                                 action_code_settings=action_code_settings)
    assert isinstance(link, str)
    query_dict = _extract_link_params(link)
    assert query_dict['continueUrl'] == ACTION_LINK_CONTINUE_URL
    user_email = _verify_email(query_dict['oobCode'], api_key)
    assert new_user_email_unverified.email == user_email
    assert auth.get_user(new_user_email_unverified.uid).email_verified

def test_email_sign_in_with_settings(new_user_email_unverified, api_key):
    action_code_settings = auth.ActionCodeSettings(ACTION_LINK_CONTINUE_URL)
    link = auth.generate_sign_in_with_email_link(new_user_email_unverified.email,
                                                 action_code_settings=action_code_settings)
    assert isinstance(link, str)
    query_dict = _extract_link_params(link)
    assert query_dict['continueUrl'] == ACTION_LINK_CONTINUE_URL
    oob_code = query_dict['oobCode']
    id_token = _sign_in_with_email_link(new_user_email_unverified.email, oob_code, api_key)
    assert id_token is not None and len(id_token) > 0
    assert auth.get_user(new_user_email_unverified.uid).email_verified

class CredentialWrapper(credentials.Base):
    """A custom Firebase credential that wraps an OAuth2 token."""

    def __init__(self, token):
        self._delegate = google.oauth2.credentials.Credentials(token)

    def get_credential(self):
        return self._delegate

    @classmethod
    def from_existing_credential(cls, google_cred):
        if not google_cred.token:
            request = transport.requests.Request()
            google_cred.refresh(request)
        return CredentialWrapper(google_cred.token)
