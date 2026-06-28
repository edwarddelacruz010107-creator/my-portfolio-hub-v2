"""
app/forms/tenant_forms.py — WTForms for per-tenant form provider settings (v4.2)

Used by:
    /admin/settings/forms         → TenantFormSettingsForm (tenant admin)
    /superadmin/forms/<tenant_id> → SuperadminFormOverviewForm (read-only view)
"""
from flask_wtf import FlaskForm
from wtforms import (
    SelectField,
    StringField,
    PasswordField,
    EmailField,
    BooleanField,
    SubmitField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    Length,
    Optional,
    URL,
    ValidationError,
)

BASIN_PREFIX   = 'https://usebasin.com/f/'
PROVIDER_CHOICES = [
    ('disabled',   'Disabled — use CMS inbox only'),
    ('basin',      'Basin — serverless form endpoint'),
    ('email_only', 'Email Only — deliver to recipient email'),
]


class TenantFormSettingsForm(FlaskForm):
    """
    Form for tenant admin to configure their own contact form provider.
    Rendered at: /admin/settings/forms
    API key input is a password field — never pre-filled with existing value.
    """

    provider = SelectField(
        'Contact Form Provider',
        choices=PROVIDER_CHOICES,
        validators=[DataRequired()],
    )

    # ── API key: password field so it is not pre-filled and not visible ───────
    api_key = PasswordField(
        'API Key',
        validators=[Optional(), Length(max=500)],
        description=(
            'Basin: leave blank to keep current. '
            'Web3Forms: your access_key from the dashboard.'
        ),
    )

    # ── Basin-specific ────────────────────────────────────────────────────────
    form_endpoint = StringField(
        'Basin Endpoint URL',
        validators=[Optional(), URL(), Length(max=500)],
        description=f'Must start with {BASIN_PREFIX}',
    )

    # ── Shared ────────────────────────────────────────────────────────────────
    receiver_email = EmailField(
        'Receiver Email',
        validators=[Optional(), Email(), Length(max=200)],
        description='Where contact form submissions are delivered.',
    )

    sender_name = StringField(
        'Sender Display Name',
        validators=[Optional(), Length(max=200)],
        description='Shown in the From field of delivered emails.',
    )

    is_enabled = BooleanField('Enable contact form', default=True)

    submit = SubmitField('Save Settings')

    # ── Cross-field validation ────────────────────────────────────────────────

    def validate_form_endpoint(self, field):
        """Basin requires a valid endpoint URL."""
        if self.provider.data == 'basin':
            if not field.data:
                raise ValidationError('Basin endpoint URL is required when provider is Basin.')
            if not field.data.startswith(BASIN_PREFIX):
                raise ValidationError(
                    f'Basin endpoint must start with {BASIN_PREFIX}'
                )
            form_id = field.data.removeprefix(BASIN_PREFIX).strip('/')
            if len(form_id) < 4:
                raise ValidationError('Basin form ID appears too short. Check the URL.')

    def validate_api_key(self, field):
        """API key length sanity check (only when a value is supplied)."""
        if field.data and len(field.data) < 8:
            raise ValidationError('API key appears too short.')

    def validate_receiver_email(self, field):
        """Receiver email is required for email_only provider and recommended for basin."""
        if self.provider.data == 'email_only' and self.is_enabled.data:
            if not field.data:
                raise ValidationError(
                    'Recipient email is required when Email Only provider is enabled.'
                )
