import json

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.utils.translation import gettext as _
from djblets.secrets.token_generators import token_generator_registry
from djblets.util.decorators import augment_method_from
from djblets.webapi.decorators import (webapi_login_required,
                                       webapi_request_fields,
                                       webapi_response_errors)
from djblets.webapi.errors import (DOES_NOT_EXIST, INVALID_FORM_DATA,
                                   NOT_LOGGED_IN, PERMISSION_DENIED,
                                   WebAPITokenGenerationError)
from djblets.webapi.fields import (BooleanFieldType,
                                   DateTimeFieldType,
                                   DictFieldType,
                                   StringFieldType)

from reviewboard.webapi.base import ImportExtraDataError, WebAPIResource
from reviewboard.webapi.decorators import webapi_check_local_site
from reviewboard.webapi.errors import TOKEN_GENERATION_FAILED
from reviewboard.webapi.models import WebAPIToken
from reviewboard.webapi.resources import resources


class TokenExpiresFieldType(DateTimeFieldType):
    """A field type for the WebAPIToken's expires field.

    This field acts the same as
    :py:class:`djblets.webapi.fields.DateTimeFieldType` except that it can
    accept null values. This allows optional date/time fields such as the
    expires field to be set to date/time values or null.

    Version Added:
        5.0
    """

    name = _('Nullable ISO 8601 Date/Time')

    def clean_value(self, value):
        """Validate and return a null value or datetime from an ISO 8601 value.

        Args:
            value (object):
                The value to validate and normalize. This should be a
                :py:class:`datetime.datetime`, an ISO 8601 date/time string
                `None` or an empty string.

        Returns:
            datetime.datetime:
            The resulting date/time value.

        Raises:
            django.core.exceptions.ValidationError:
                The resulting value was not a null value, a valid ISO 8601
                date/time string or the time was ambiguous.
        """
        if value is None or value == '':
            return None

        return super(TokenExpiresFieldType, self).clean_value(value)


class APITokenResource(WebAPIResource):
    """Manages the tokens used to access the API.

    This resource allows callers to retrieve their list of tokens, register
    new tokens, delete old ones, and update information on existing tokens.
    """
    model = WebAPIToken
    name = 'api_token'
    verbose_name = 'API Token'

    api_token_access_allowed = False
    oauth2_token_access_allowed = False

    added_in = '2.5'

    fields = {
        'expires': {
            'type': TokenExpiresFieldType,
            'description': 'An optional field for the date and time that the '
                           'token will expire. The token will be invalid and '
                           'unusable for authentication after this point.',
            'added_in': '5.0',
        },
        'expired': {
            'type': BooleanFieldType,
            'description': 'Whether the token is expired.',
            'added_in': '5.0',
        },
        'extra_data': {
            'type': DictFieldType,
            'description': 'Extra data as part of the token. '
                           'This can be set by the API or extensions.',
        },
        'id': {
            'type': StringFieldType,
            'description': 'The numeric ID of the token entry.',
        },
        'invalid_date': {
            'type': DateTimeFieldType,
            'description': 'The date and time at which the token became '
                           'invalid.',
            'added_in': '5.0',
        },
        'invalid_reason': {
            'type': StringFieldType,
            'description': 'A message explaining why the token is no longer '
                           'valid.',
            'added_in': '5.0',
        },
        'last_updated': {
            'type': DateTimeFieldType,
            'description': 'The date and time that the token was last '
                           'updated.',
        },
        'last_used': {
            'type': DateTimeFieldType,
            'description': 'The date and time that the token was last '
                           'used for authentication.',
            'added_in': '5.0',
        },
        'note': {
            'type': StringFieldType,
            'description': 'The note explaining the purpose of this token.',
        },
        'policy': {
            'type': DictFieldType,
            'description': 'The access policies defined for this token.',
        },
        'time_added': {
            'type': DateTimeFieldType,
            'description': 'The date and time that the token was added.',
        },
        'token': {
            'type': StringFieldType,
            'description': 'The token value.',
        },
        'token_generator_id': {
            'type': StringFieldType,
            'description': 'The ID of the token generator that generated '
                           'the token.',
            'added_in': '5.0',
        },
        'valid': {
            'type': BooleanFieldType,
            'description': 'Whether the token is currently valid.',
            'added_in': '5.0',
        },
    }

    uri_object_key = 'api_token_id'
    last_modified_field = 'last_updated'
    model_parent_key = 'user'

    allowed_methods = ('GET', 'POST', 'PUT', 'DELETE')

    def get_queryset(self, request, local_site_name=None, *args, **kwargs):
        user = resources.user.get_object(
            request, local_site_name=local_site_name, *args, **kwargs)

        local_site = self._get_local_site(local_site_name)

        return self.model.objects.filter(user=user, local_site=local_site)

    def has_list_access_permissions(self, request, *args, **kwargs):
        if request.user.is_superuser:
            return True

        user = resources.user.get_object(request, *args, **kwargs)
        return user == request.user

    def has_access_permissions(self, request, token, *args, **kwargs):
        return token.is_accessible_by(request.user)

    def has_modify_permissions(self, request, token, *args, **kwargs):
        return token.is_mutable_by(request.user)

    def has_delete_permissions(self, request, token, *args, **kwargs):
        return token.is_deletable_by(request.user)

    def serialize_expires_field(self, obj, *args, **kwargs):
        """Serialize the ``expires`` field.

        Version Added:
            5.0

        Args:
            obj (reviewboard.webapi.models.WebAPIToken):
                The token that is being serialized.

            *args (tuple):
                Unused positional arguments.

            **kwargs (dict):
                Unused keyword arguments.

        Returns:
            str:
            The expiration date of the token, in ISO-8601 format.
        """
        if obj.expires:
            return obj.expires.isoformat()
        else:
            return None

    def serialize_expired_field(self, obj, *args, **kwargs):
        """Serialize the ``expired`` field.

        Version Added:
            5.0

        Args:
            obj (reviewboard.webapi.models.WebAPIToken):
                The token that is being serialized.

            *args (tuple):
                Unused positional arguments.

            **kwargs (dict):
                Unused keyword arguments.

        Returns:
            bool:
            Whether the API token is expired.
        """
        return obj.is_expired()

    def serialize_invalid_date_field(self, obj, *args, **kwargs):
        """Serialize the ``invalid_date`` field.

        Version Added:
            5.0

        Args:
            obj (reviewboard.webapi.models.WebAPIToken):
                The token that is being serialized.

            *args (tuple):
                Unused positional arguments.

            **kwargs (dict):
                Unused keyword arguments.

        Returns:
            str:
            The invalid date of the token, in ISO-8601 format.
        """
        if obj.invalid_date:
            return obj.invalid_date.isoformat()
        else:
            return None

    def serialize_last_updated_field(self, obj, *args, **kwargs):
        """Serialize the ``last_updated`` field.

        Version Added:
            5.0

        Args:
            obj (reviewboard.webapi.models.WebAPIToken):
                The token that is being serialized.

            *args (tuple):
                Unused positional arguments.

            **kwargs (dict):
                Unused keyword arguments.

        Returns:
            str:
            The last updated date of the token, in ISO-8601 format.
        """
        return obj.last_updated.isoformat()

    def serialize_last_used_field(self, obj, *args, **kwargs):
        """Serialize the ``last_used`` field.

        Version Added:
            5.0

        Args:
            obj (reviewboard.webapi.models.WebAPIToken):
                The token that is being serialized.

            *args (tuple):
                Unused positional arguments.

            **kwargs (dict):
                Unused keyword arguments.

        Returns:
            str:
            The last used date of the token, in ISO-8601 format.
        """
        if obj.last_used:
            return obj.last_used.isoformat()
        else:
            return None

    def serialize_time_added_field(self, obj, *args, **kwargs):
        """Serialize the ``time_added`` field.

        Version Added:
            5.0

        Args:
            obj (reviewboard.webapi.models.WebAPIToken):
                The token that is being serialized.

            *args (tuple):
                Unused positional arguments.

            **kwargs (dict):
                Unused keyword arguments.

        Returns:
            str:
            The time added date of the token, in ISO-8601 format.
        """
        return obj.time_added.isoformat()

    @webapi_check_local_site
    @webapi_login_required
    @webapi_response_errors(DOES_NOT_EXIST, INVALID_FORM_DATA, NOT_LOGGED_IN,
                            PERMISSION_DENIED, TOKEN_GENERATION_FAILED)
    @webapi_request_fields(
        optional={
            'expires': {
                'type': TokenExpiresFieldType,
                'description': 'The date and time that the token will expire.'
                               'This must be a valid '
                               ':term:`date/time format`.',
                'added_in': '5.0',
            },
        },
        required={
            'note': {
                'type': StringFieldType,
                'description': 'The note explaining the purpose of '
                               'this token.',
            },
            'policy': {
                'type': StringFieldType,
                'description': 'The token access policy, encoded as a '
                               'JSON string.',
            },
        },
        allow_unknown=True
    )
    def create(self, request, note, policy, extra_fields={},
               local_site_name=None, *args, **kwargs):
        """Registers a new API token.

        The token value will be generated and returned in the payload.

        Callers are expected to provide a note and a policy.

        Note that this may, in theory, fail due to too many token collisions.
        If that happens, please re-try the request.

        Extra data can be stored later lookup. See
        :ref:`webapi2.0-extra-data` for more information.
        """
        try:
            user = resources.user.get_object(request, *args, **kwargs)
        except ObjectDoesNotExist:
            return DOES_NOT_EXIST

        if not self.has_list_access_permissions(request, *args, **kwargs):
            return self.get_no_access_error(request)

        try:
            self._validate_policy(policy)
        except ValueError as e:
            return INVALID_FORM_DATA, {
                'fields': {
                    'policy': str(e),
                },
            }

        local_site = self._get_local_site(local_site_name)
        expires = kwargs.get('expires', None)
        token_generator_id = \
            token_generator_registry.get_default().token_generator_id

        try:
            token = WebAPIToken.objects.generate_token(
                user,
                token_generator_id=token_generator_id,
                token_info={'token_type': 'rbp'},
                note=note,
                policy=policy,
                expires=expires,
                local_site=local_site)
        except WebAPITokenGenerationError as e:
            return TOKEN_GENERATION_FAILED.with_message(str(e))

        if extra_fields:
            try:
                self.import_extra_data(token, token.extra_data, extra_fields)
            except ImportExtraDataError as e:
                return e.error_payload

            token.save(update_fields=('extra_data',))

        return 201, {
            self.item_result_key: token,
        }

    @webapi_check_local_site
    @webapi_login_required
    @webapi_response_errors(DOES_NOT_EXIST, INVALID_FORM_DATA, NOT_LOGGED_IN,
                            PERMISSION_DENIED)
    @webapi_request_fields(
        optional={
            'expires': {
                'type': TokenExpiresFieldType,
                'description': 'The date and time that the token will expire.'
                               'This must be a valid '
                               ':term:`date/time format`.',
                'added_in': '5.0',
            },
            'invalid_reason': {
                'type': StringFieldType,
                'description': 'A message indicating why the token is '
                               'no longer valid.',
                'added_in': '5.0',
            },
            'note': {
                'type': StringFieldType,
                'description': 'The note explaining the purpose of '
                               'this token.',
            },
            'policy': {
                'type': StringFieldType,
                'description': 'The token access policy, encoded as a '
                               'JSON string.',
            },
            'valid': {
                'type': BooleanFieldType,
                'description': 'Whether the token is valid. This can only be '
                               'used to invalidate tokens by setting this to '
                               '``false``. Setting this to ``true`` is not '
                               'allowed and will cause an invalid form data '
                               'error.',
                'added_in': '5.0',
            },
        },
        allow_unknown=True
    )
    def update(self, request, extra_fields={}, *args, **kwargs):
        """Updates the information on an existing API token.

        The note, policy, and extra data on the token may be updated.
        See :ref:`webapi2.0-extra-data` for more information.

        This can also be used to invalidate a token by setting
        valid to ``false`` and including an invalid reason.
        """
        try:
            token = self.get_object(request, *args, **kwargs)
        except ObjectDoesNotExist:
            return DOES_NOT_EXIST

        if not self.has_access_permissions(request, token, *args, **kwargs):
            return self.get_no_access_error(request)

        if 'expires' in kwargs:
            token.expires = kwargs['expires']

        if 'note' in kwargs:
            token.note = kwargs['note']

        if 'policy' in kwargs:
            try:
                token.policy = self._validate_policy(kwargs['policy'])
            except ValidationError as e:
                return INVALID_FORM_DATA, {
                    'fields': {
                        'policy': e.message,
                    },
                }

        valid = kwargs.get('valid')

        if valid is True:
            return INVALID_FORM_DATA, {
                'fields': {
                    'valid': _('This can only be used to invalidate the '
                               'token. You cannot set valid to true.')
                }
            }
        elif valid is False:
            self.model.objects.invalidate_token(
                token=token.token,
                invalid_reason=kwargs.get('invalid_reason', ''))

        if extra_fields:
            try:
                self.import_extra_data(token, token.extra_data, extra_fields)
            except ImportExtraDataError as e:
                return e.error_payload

        token.save()

        return 200, {
            self.item_result_key: token,
        }

    @augment_method_from(WebAPIResource)
    def delete(self, *args, **kwargs):
        """Delete the API token, invalidating all clients using it.

        The API token will be removed from the user's account, and will no
        longer be usable for authentication.

        After deletion, this will return a :http:`204`.
        """
        pass

    @webapi_check_local_site
    @augment_method_from(WebAPIResource)
    def get_list(self, *args, **kwargs):
        """Retrieves a list of API tokens belonging to a user.

        If accessing this API on a Local Site, the results will be limited
        to those associated with that site.

        This can only be accessed by the owner of the tokens, or superusers.
        """
        pass

    @webapi_check_local_site
    @augment_method_from(WebAPIResource)
    def get(self, *args, **kwargs):
        """Retrieves information on a particular API token.

        This can only be accessed by the owner of the tokens, or superusers.
        """
        pass

    def _validate_policy(self, policy_str):
        try:
            policy = json.loads(policy_str)
        except Exception as e:
            raise ValidationError(
                _('The policy is not valid JSON: %s')
                % str(e))

        self.model.validate_policy(policy)

        return policy


api_token_resource = APITokenResource()
