# -*- coding:utf-8 -*-
import hashlib
import json
import os
import shutil
from datetime import datetime

import cloudscraper
from requests.structures import CaseInsensitiveDict

from .utils import PixivError, JsonDict


class BasePixivAPI(object):
    client_id = 'MOBrBDS8blbauoSck0ZfDbtuzpyT'
    client_secret = 'lsACyCD94FhDUtGTXi3QzcFE2uU1hqtDaKeqrdwj'
    hash_secret = '28c1fdd170a5204386cb1313c7077b34f83e4aaf4aa829ce78c231e05b0bae2c'

    def __init__(self, **requests_kwargs):
        """initialize requests kwargs if need be"""
        self.user_id = 0
        self.access_token = None
        self.refresh_token = None
        # self.requests = requests.Session()
        self.requests = cloudscraper.create_scraper()  # fix due to #140
        self.additional_headers = CaseInsensitiveDict(requests_kwargs.pop('headers', {}))
        self.requests_kwargs = requests_kwargs

    def set_additional_headers(self, headers):
        """manually specify additional headers. will overwrite API default headers in case of collision"""
        self.additional_headers = CaseInsensitiveDict(headers)

    # 设置HTTP的Accept-Language (用于获取tags的对应语言translated_name)
    # language: en-us, zh-cn, ...
    def set_accept_language(self, language):
        """set header Accept-Language for all requests (useful for get tags.translated_name)"""
        self.additional_headers['Accept-Language'] = language

    @classmethod
    def parse_json(cls, json_str):
        """parse str into JsonDict"""
        return json.loads(json_str, object_hook=JsonDict)

    def require_auth(self):
        if self.access_token is None:
            raise PixivError('Authentication required! Call login() or set_auth() first!')

    def requests_call(self, method, url, headers=None, params=None, data=None, stream=False):
        """ requests http/https call for Pixiv API """
        merged_headers = self.additional_headers.copy()
        if headers:
            # Use the headers in the parameter to override the
            # additional_headers setting.
            merged_headers.update(headers)
        try:
            if method == 'GET':
                return self.requests.get(
                    url, params=params,
                    headers=merged_headers, stream=stream,
                    **self.requests_kwargs
                )
            elif method == 'POST':
                return self.requests.post(
                    url, params=params, data=data,
                    headers=merged_headers, stream=stream,
                    **self.requests_kwargs
                )
            elif method == 'DELETE':
                return self.requests.delete(
                    url, params=params, data=data,
                    headers=merged_headers, stream=stream,
                    **self.requests_kwargs
                )
        except Exception as e:
            raise PixivError('requests %s %s error: %s' % (method, url, e))

        raise PixivError('Unknown method: %s' % method)

    def set_auth(self, access_token, refresh_token=None):
        self.access_token = access_token
        self.refresh_token = refresh_token

    def login(self, username, password):
        return self.auth(username=username, password=password)

    def set_client(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret

    def auth(self, username=None, password=None, refresh_token=None, headers=None):
        """Login with password, or use the refresh_token to acquire a new bearer token"""
        local_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S+00:00')
        headers = CaseInsensitiveDict(headers or {})
        headers['x-client-time'] = local_time
        headers['x-client-hash'] = hashlib.md5((local_time + self.hash_secret).encode('utf-8')).hexdigest()
        # Allow mock UA due to #171: https://github.com/upbit/pixivpy/issues/171
        if 'user-agent' not in headers:
            headers['app-os'] = 'ios'
            headers['app-os-version'] = '14.6'
            headers['user-agent'] = 'PixivIOSApp/7.13.3 (iOS 14.6; iPhone13,2)'

        # noinspection PyUnresolvedReferences
        if not hasattr(self, 'hosts') or self.hosts == 'https://app-api.pixiv.net':
            auth_hosts = 'https://oauth.secure.pixiv.net'
        else:
            # noinspection PyUnresolvedReferences
            auth_hosts = self.hosts  # BAPI解析成IP的场景
            headers['host'] = 'oauth.secure.pixiv.net'
        url = '%s/auth/token' % auth_hosts
        data = {
            'get_secure_url': 1,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
        }

        if username and password:
            data['grant_type'] = 'password'
            data['username'] = username
            data['password'] = password
        elif refresh_token or self.refresh_token:
            data['grant_type'] = 'refresh_token'
            data['refresh_token'] = refresh_token or self.refresh_token
        else:
            raise PixivError('[ERROR] auth() but no password or refresh_token is set.')

        r = self.requests_call('POST', url, headers=headers, data=data)
        if r.status_code not in {200, 301, 302}:
            if data['grant_type'] == 'password':
                raise PixivError(
                    '[ERROR] auth() failed! check username and password.\nHTTP %s: %s' % (r.status_code, r.text),
                    header=r.headers, body=r.text,
                )
            else:
                raise PixivError(
                    '[ERROR] auth() failed! check refresh_token.\nHTTP %s: %s' % (r.status_code, r.text),
                    header=r.headers, body=r.text,
                )

        token = None
        try:
            # get access_token
            token = self.parse_json(r.text)
            self.user_id = token.response.user.id
            self.access_token = token.response.access_token
            self.refresh_token = token.response.refresh_token
        except json.JSONDecodeError:
            raise PixivError('Get access_token error! Response: %s' % token, header=r.headers, body=r.text)

        # return auth/token response
        return token

    def download(self, url, prefix='', path=os.path.curdir, name=None, replace=False, fname=None,
                 referer='https://app-api.pixiv.net/'):
        """Download image to file (use 6.0 app-api)"""
        if hasattr(fname, 'write'):
            # A file-like object has been provided.
            file = fname
        else:
            # Determine file path by parameters.
            name = prefix + (name or fname or os.path.basename(url))
            file = os.path.join(path, name)
            if os.path.exists(file) and not replace:
                return False

        with self.requests_call('GET', url, headers={'Referer': referer}, stream=True) as response:
            if isinstance(file, str):
                with open(file, 'wb') as out_file:
                    shutil.copyfileobj(response.raw, out_file)
            else:
                shutil.copyfileobj(response.raw, file)
        return True
