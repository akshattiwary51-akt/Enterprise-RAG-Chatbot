"""Request schemas. Unknown fields are REJECTED (400) so a client cannot smuggle in
extra parameters such as `role`, `user_id`, `document_ids` or vector-store filters."""
from __future__ import annotations

from marshmallow import RAISE, Schema, ValidationError, fields, validate, validates_schema

from ..models import CLASSIFICATIONS


class StrictSchema(Schema):
    class Meta:
        unknown = RAISE


class LoginSchema(StrictSchema):
    email = fields.Str(required=True, validate=validate.Length(min=3, max=255))
    password = fields.Str(required=True, validate=validate.Length(min=1, max=256))


class RefreshLogoutSchema(StrictSchema):
    refresh_token = fields.Str(load_default=None, validate=validate.Length(max=4096))


class ChangePasswordSchema(StrictSchema):
    current_password = fields.Str(required=True, validate=validate.Length(min=1, max=256))
    new_password = fields.Str(required=True, validate=validate.Length(min=1, max=256))


class CreateUserSchema(StrictSchema):
    email = fields.Email(required=True, validate=validate.Length(max=255))
    full_name = fields.Str(required=True, validate=validate.Length(min=1, max=200))
    password = fields.Str(required=True, validate=validate.Length(min=1, max=256))
    roles = fields.List(fields.Str(validate=validate.Length(min=1, max=64)), load_default=lambda: ["employee"],
                        validate=validate.Length(min=1, max=10))
    department = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=64))


class UpdateUserSchema(StrictSchema):
    full_name = fields.Str(validate=validate.Length(min=1, max=200))
    is_active = fields.Bool()
    department = fields.Str(allow_none=True, validate=validate.Length(max=64))
    password = fields.Str(validate=validate.Length(min=1, max=256))  # admin reset => must change on next login


class UpdateRolesSchema(StrictSchema):
    roles = fields.List(fields.Str(validate=validate.Length(min=1, max=64)), required=True,
                        validate=validate.Length(min=1, max=10))


class UploadFormSchema(StrictSchema):
    allowed_roles = fields.List(fields.Str(), load_default=list)
    user_grants = fields.List(fields.Str(), load_default=list)
    classification = fields.Str(load_default="internal", validate=validate.OneOf(CLASSIFICATIONS))
    owner_team = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=64))
    department = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=64))


class UpdatePermissionsSchema(StrictSchema):
    allowed_roles = fields.List(fields.Str(validate=validate.Length(max=64)), required=True,
                                validate=validate.Length(max=20))
    user_grants = fields.List(fields.Str(validate=validate.Length(max=36)), load_default=list,
                              validate=validate.Length(max=200))
    classification = fields.Str(required=True, validate=validate.OneOf(CLASSIFICATIONS))
    department = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=64))
    owner_team = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=64))


class ChatSchema(StrictSchema):
    message = fields.Str(required=True, validate=validate.Length(min=1, max=4000))
    conversation_id = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=36))

    @validates_schema
    def _not_blank(self, data, **_):
        if not data.get("message", "").strip():
            raise ValidationError("message must not be blank", "message")


class PageSchema(Schema):
    class Meta:
        unknown = RAISE

    page = fields.Int(load_default=1, validate=validate.Range(min=1, max=10_000))
    per_page = fields.Int(load_default=20, validate=validate.Range(min=1, max=100))


class AuditQuerySchema(PageSchema):
    event_type = fields.Str(load_default=None, validate=validate.Length(max=64))
    result = fields.Str(load_default=None, validate=validate.OneOf(["success", "failure", "denied"]))


def load_json(schema: Schema, payload) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError({"_schema": ["JSON object body required"]})
    return schema.load(payload)
