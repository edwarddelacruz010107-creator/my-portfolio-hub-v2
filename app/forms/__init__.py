"""
app/forms/__init__.py — All WTForms form classes

All forms use Flask-WTF (CSRF protection enabled by default).
URL fields gracefully handle empty strings (Optional + URL validators).

Security v3.1: Password policy validation integrated into forms.
"""
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from flask_login import current_user
from wtforms import (
    StringField, TextAreaField, IntegerField, BooleanField,
    SelectField, URLField, PasswordField, EmailField, DateField,
    HiddenField, SubmitField, DecimalField,
)
from wtforms.validators import (
    DataRequired, Optional, Length, NumberRange,
    Email, URL, EqualTo, ValidationError,
)
from app.security import PasswordPolicy


def password_policy_check(form, field):
    """WTForms validator for password policy."""
    is_valid, error_msg = PasswordPolicy.validate(field.data)
    if not is_valid:
        raise ValidationError(error_msg)


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginForm(FlaskForm):
    username   = StringField('Username or Email', validators=[DataRequired(), Length(max=120)])
    password   = PasswordField('Password',        validators=[DataRequired()])
    remember_me = BooleanField('Keep me signed in')
    submit     = SubmitField('Sign In')


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password     = PasswordField('New Password',
                                      validators=[DataRequired(), password_policy_check])
    confirm_password = PasswordField('Confirm New Password',
                                      validators=[DataRequired(),
                                                  EqualTo('new_password',
                                                           message='Passwords must match.')])
    submit = SubmitField('Change Password')


class RegisterForm(FlaskForm):
    """Public signup form — used by /auth/register and the /auth portal's
    Create Account tab. Was defined in app/forms_patch/FORMS_ADDITIONS.py
    but never actually merged into this module, so importing it raised
    ImportError; moved here for real rather than left as an unapplied patch."""
    username         = StringField('Username',
                                    validators=[DataRequired(), Length(min=3, max=64)])
    full_name        = StringField('Full Name',
                                    validators=[DataRequired(), Length(min=2, max=100)])
    email            = EmailField('Email',
                                    validators=[DataRequired(), Email(), Length(max=120)])
    password         = PasswordField('Password',
                                    validators=[DataRequired(), password_policy_check])
    confirm_password = PasswordField('Confirm Password',
                                    validators=[DataRequired(),
                                                EqualTo('password',
                                                        message='Passwords must match.')])
    accept_terms     = BooleanField('I accept the Terms of Service and Privacy Policy',
                                    validators=[DataRequired(message='You must accept the terms.')])
    submit           = SubmitField('Create Account')

    def validate_username(self, field):
        from app.models import User
        username = field.data.strip()
        if ' ' in username:
            raise ValidationError('Username cannot contain spaces.')
        existing = User.query.filter(User.username == username).first()
        if existing:
            raise ValidationError('That username is already taken.')

    def validate_email(self, field):
        from app.services.auth.email_policy import EmailPolicyError, assert_public_signup_email_allowed

        try:
            field.data = assert_public_signup_email_allowed(field.data)
        except EmailPolicyError as exc:
            raise ValidationError(str(exc))


class ResendVerificationForm(FlaskForm):
    email  = EmailField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Resend verification email')


class EmailOTPForm(FlaskForm):
    """6-digit signup email-verification code. CSRF-protected like every
    other form here; the code itself is validated server-side against the
    hashed PasswordResetOTP record (user_type='email_verify')."""
    code = StringField(
        'Verification code',
        validators=[DataRequired(), Length(min=6, max=6, message='Enter the 6-digit code.')],
    )
    submit = SubmitField('Verify')


class SuperadminAccountForm(FlaskForm):
    username = StringField(
        'Username',
        validators=[DataRequired(), Length(min=3, max=64)],
    )
    email = EmailField(
        'Admin Email',
        validators=[DataRequired(), Email(), Length(max=120)],
    )
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    submit = SubmitField('Update Account')

    def validate_username(self, field):
        from app.models import User

        if field.data.strip() != current_user.username:
            existing = User.query.filter(User.username == field.data.strip()).first()
            if existing:
                raise ValidationError('That username is already taken.')

    def validate_email(self, field):
        from app.services.auth.email_policy import EmailPolicyError, assert_email_allowed_for_user

        try:
            field.data = assert_email_allowed_for_user(
                field.data,
                user=current_user,
                tenant=getattr(current_user, 'tenant', None),
                role='superadmin' if getattr(current_user, 'is_superadmin', False) else 'tenant_admin',
            )
        except EmailPolicyError as exc:
            raise ValidationError(str(exc))


# ── Profile ───────────────────────────────────────────────────────────────────

class ProfileForm(FlaskForm):
    name     = StringField('Full Name', validators=[DataRequired(), Length(max=100)])
    title    = StringField('Professional Title', validators=[Optional(), Length(max=150)])
    subtitle = StringField('Subtitle / Tagline',  validators=[Optional(), Length(max=200)])
    bio      = TextAreaField('Full Bio',           validators=[Optional()])
    bio_short = StringField('Short Bio',           validators=[Optional(), Length(max=300)])
    location = StringField('Location',             validators=[Optional(), Length(max=100)])
    email    = EmailField('Contact Email',         validators=[Optional(), Email()])
    phone    = StringField('Phone',                validators=[Optional(), Length(max=30)])

    profile_image = FileField(
        'Profile Picture',
        validators=[FileAllowed(['jpg', 'jpeg', 'png', 'webp', 'gif'],
                                 'Images only (JPG, PNG, WebP, GIF).')]
    )
    resume_url = URLField('Resume URL', validators=[Optional(), URL()])

    years_experience      = IntegerField('Years of Experience',
                                          validators=[Optional(), NumberRange(min=0, max=99)])
    experience_start_year = IntegerField('Experience Start Year',
                                          validators=[Optional(), NumberRange(min=1990, max=2035)])
    clients_count  = IntegerField('Clients Count', validators=[Optional(), NumberRange(min=0)])
    hero_tagline   = StringField('Hero Tagline',   validators=[Optional(), Length(max=200)])
    availability_status = StringField('Availability Status',
                                       validators=[Optional(), Length(max=100)])
    is_available   = BooleanField('Currently Available for Work')

    # Social links
    github    = URLField('GitHub',           validators=[Optional(), URL()])
    linkedin  = URLField('LinkedIn',         validators=[Optional(), URL()])
    twitter   = URLField('Twitter / X',      validators=[Optional(), URL()])
    instagram = URLField('Instagram',        validators=[Optional(), URL()])
    youtube   = URLField('YouTube',          validators=[Optional(), URL()])
    website   = URLField('Personal Website', validators=[Optional(), URL()])
    facebook  = URLField('Facebook',         validators=[Optional(), URL()])
    dribbble  = URLField('Dribbble',         validators=[Optional(), URL()])

    submit = SubmitField('Save Profile')


class TenantForm(FlaskForm):
    name = StringField('Client / Portfolio Name', validators=[DataRequired(), Length(max=100)])
    tenant_slug = StringField('Slug (URL identifier)', validators=[DataRequired(), Length(max=120)])
    contact_email = EmailField(
        'Contact Email *',
        validators=[DataRequired(), Email(), Length(max=120)],
        description='Destination for contact-form submissions and password recovery OTPs.',
    )
    plan = SelectField(
        'Plan',
        choices=[
            ('Trial', 'Trial (not subscribed)'),
            ('Basic', 'Basic'),
            ('Pro', 'Pro'),
            ('Enterprise', 'Enterprise'),
        ],
        default='Trial',
        validators=[Optional()],
    )
    monthly_rate = StringField('Monthly Rate (P / $)', validators=[Optional()])
    free_trial_days = IntegerField('Free Trial Days', validators=[Optional(), NumberRange(min=0, max=365)], default=14)
    internal_notes = TextAreaField('Internal Notes', validators=[Optional()])
    admin_username = StringField('Admin Username', validators=[DataRequired(), Length(min=3, max=80)])
    admin_email = EmailField('Admin Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Save Tenant')


class LandingPageSettingsForm(FlaskForm):
    hero_badge = StringField('Hero Badge', validators=[Optional(), Length(max=80)], render_kw={'maxlength': 80})
    hero_title = StringField('Hero Headline', validators=[Optional(), Length(max=200)], render_kw={'maxlength': 200})
    hero_subtitle = TextAreaField('Hero Subtitle', validators=[Optional(), Length(max=400)], render_kw={'maxlength': 400})
    hero_cta_primary_text = StringField('Primary CTA Label', validators=[Optional(), Length(max=80)], render_kw={'maxlength': 80})
    hero_cta_primary_url = StringField('Primary CTA URL', validators=[Optional(), Length(max=255)], render_kw={'maxlength': 255})
    hero_cta_secondary_text = StringField('Secondary CTA Label', validators=[Optional(), Length(max=80)], render_kw={'maxlength': 80})
    hero_cta_secondary_url = StringField('Secondary CTA URL', validators=[Optional(), Length(max=255)], render_kw={'maxlength': 255})
    hero_image_url = StringField('Hero Visual URL', validators=[Optional(), Length(max=500)], render_kw={'maxlength': 500})
    hero_preview_name = StringField('Preview Profile Name', validators=[Optional(), Length(max=80)], render_kw={'maxlength': 80})
    hero_preview_role = StringField('Preview Profile Role', validators=[Optional(), Length(max=100)], render_kw={'maxlength': 100})
    hero_preview_url_text = StringField('Preview Browser URL Text', validators=[Optional(), Length(max=80)], render_kw={'maxlength': 80})
    hero_stat_badge_text = StringField('Floating Badge Text', validators=[Optional(), Length(max=60)], render_kw={'maxlength': 60})
    hero_stat_likes = StringField('Likes Stat', validators=[Optional(), Length(max=20)], render_kw={'maxlength': 20})
    hero_stat_views = StringField('Views Stat', validators=[Optional(), Length(max=20)], render_kw={'maxlength': 20})
    hero_stat_comments = StringField('Comments Stat', validators=[Optional(), Length(max=20)], render_kw={'maxlength': 20})
    hero_enable_widgets = BooleanField('Show floating widgets', validators=[Optional()])
    hero_enable_animation = BooleanField('Enable hero animation', validators=[Optional()])
    features_heading = StringField('Features Heading', validators=[Optional(), Length(max=120)], render_kw={'maxlength': 120})
    features_subtitle = TextAreaField('Features Subtitle', validators=[Optional(), Length(max=300)], render_kw={'maxlength': 300})
    contact_heading = StringField('Contact Section Heading', validators=[Optional(), Length(max=120)], render_kw={'maxlength': 120})
    contact_subtitle = TextAreaField('Contact Section Subtitle', validators=[Optional(), Length(max=300)], render_kw={'maxlength': 300})
    founder_photo_url = StringField('Founder Photo URL', validators=[Optional(), Length(max=500)], render_kw={'maxlength': 500})
    founder_preview_image = StringField('Founder Preview Image URL', validators=[Optional(), Length(max=500)], render_kw={'maxlength': 500})
    founder_role = StringField('Founder Role', validators=[Optional(), Length(max=100)], render_kw={'maxlength': 100})
    founder_title = StringField('Founder Title', validators=[Optional(), Length(max=100)], render_kw={'maxlength': 100})
    founder_name = StringField('Founder Name', validators=[Optional(), Length(max=100)], render_kw={'maxlength': 100})
    founder_description = TextAreaField('Founder Description', validators=[Optional(), Length(max=400)], render_kw={'maxlength': 400})
    founder_portfolio_url = StringField('Founder Portfolio URL', validators=[Optional(), Length(max=255)], render_kw={'maxlength': 255})
    founder_contact_url = StringField('Founder Contact URL', validators=[Optional(), Length(max=255)], render_kw={'maxlength': 255})
    submit = SubmitField('Save Landing Content')


class PricingSettingsForm(FlaskForm):
    """Superadmin Pricing CMS — marketing-only overrides on top of the
    code-owned app.utils.BILLING_PLANS (price amounts are never editable
    here; see app/public/services/pricing_service.py for the split)."""

    # Section copy
    heading = StringField('Section Heading', validators=[Optional(), Length(max=120)], render_kw={'maxlength': 120})
    subtitle = TextAreaField('Section Subtitle', validators=[Optional(), Length(max=300)], render_kw={'maxlength': 300})
    footnote = TextAreaField('Footnote (shown when checkout is disabled)', validators=[Optional(), Length(max=300)], render_kw={'maxlength': 300})
    yearly_toggle_enabled = BooleanField('Show monthly / yearly toggle', validators=[Optional()])

    # Basic plan
    basic_badge_text = StringField('Badge Text', validators=[Optional(), Length(max=40)], render_kw={'maxlength': 40})
    basic_cta_text = StringField('CTA Label', validators=[Optional(), Length(max=40)], render_kw={'maxlength': 40})
    basic_cta_url = StringField('CTA URL', validators=[Optional(), Length(max=255)], render_kw={'maxlength': 255})
    basic_description_override = TextAreaField('Description Override', validators=[Optional(), Length(max=200)], render_kw={'maxlength': 200})
    basic_features_override = TextAreaField('Feature List Override (one per line)', validators=[Optional(), Length(max=1000)], render_kw={'maxlength': 1000})
    basic_highlighted = BooleanField('Highlight this plan', validators=[Optional()])

    # Pro plan
    pro_badge_text = StringField('Badge Text', validators=[Optional(), Length(max=40)], render_kw={'maxlength': 40})
    pro_cta_text = StringField('CTA Label', validators=[Optional(), Length(max=40)], render_kw={'maxlength': 40})
    pro_cta_url = StringField('CTA URL', validators=[Optional(), Length(max=255)], render_kw={'maxlength': 255})
    pro_description_override = TextAreaField('Description Override', validators=[Optional(), Length(max=200)], render_kw={'maxlength': 200})
    pro_features_override = TextAreaField('Feature List Override (one per line)', validators=[Optional(), Length(max=1000)], render_kw={'maxlength': 1000})
    pro_highlighted = BooleanField('Highlight this plan', validators=[Optional()])

    # Enterprise plan
    enterprise_badge_text = StringField('Badge Text', validators=[Optional(), Length(max=40)], render_kw={'maxlength': 40})
    enterprise_cta_text = StringField('CTA Label', validators=[Optional(), Length(max=40)], render_kw={'maxlength': 40})
    enterprise_cta_url = StringField('CTA URL', validators=[Optional(), Length(max=255)], render_kw={'maxlength': 255})
    enterprise_description_override = TextAreaField('Description Override', validators=[Optional(), Length(max=200)], render_kw={'maxlength': 200})
    enterprise_features_override = TextAreaField('Feature List Override (one per line)', validators=[Optional(), Length(max=1000)], render_kw={'maxlength': 1000})
    enterprise_highlighted = BooleanField('Highlight this plan', validators=[Optional()])

    submit = SubmitField('Save Pricing Content')


class LandingContactForm(FlaskForm):
    full_name = StringField('Full name', validators=[DataRequired(), Length(min=2, max=120)])
    email = EmailField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    subject = StringField('Subject', validators=[DataRequired(), Length(max=200)])
    message = TextAreaField('Message', validators=[DataRequired(), Length(min=10, max=5000)])
    phone = StringField('Phone', validators=[Optional(), Length(max=50)])
    company = StringField('Company', validators=[Optional(), Length(max=200)])
    honeypot = HiddenField('', validators=[Length(max=0)])
    submit = SubmitField('Send Message')


class PlanSelectionForm(FlaskForm):
    plan = SelectField(
        'Choose a plan',
        choices=[
            ('Basic', 'Basic'),
            ('Pro', 'Pro'),
            ('Enterprise', 'Enterprise'),
        ],
        validators=[DataRequired()],
    )
    submit = SubmitField('Save Plan')


class PaymentUploadForm(FlaskForm):
    payment_method_id = HiddenField(validators=[DataRequired(message='Please select a payment method.')])
    payment_reference = StringField(
        'Transaction Reference / ID',
        validators=[DataRequired(message='Please enter your transaction reference or ID.'), Length(max=255)],
    )
    amount_paid = StringField('Amount Paid (₱)', validators=[DataRequired(message='Please enter the amount paid.'), Length(max=32)])
    payment_note = TextAreaField('Note (Optional)', validators=[Optional(), Length(max=500)])
    payment_proof = FileField(
        'Upload Proof (Image or PDF)',
        validators=[FileAllowed(['png', 'jpg', 'jpeg', 'webp', 'pdf'], 'Images and PDF files only.')],
    )
    submit = SubmitField('Submit Payment')


class PaymentMethodForm(FlaskForm):
    name = StringField('Method Name', validators=[DataRequired(), Length(max=120)])
    method_type = SelectField(
        'Type',
        choices=[
            ('ewallet', 'E-Wallet (GCash, Maya, etc.)'),
            ('bank', 'Bank Transfer'),
            ('paymongo', 'PayMongo'),
            ('crypto', 'Crypto'),
        ],
        validators=[DataRequired()],
        default='ewallet',
    )
    instructions = TextAreaField('Payment Instructions', validators=[Optional(), Length(max=2000)])
    account_name = StringField('Account Name', validators=[Optional(), Length(max=120)])
    account_number = StringField('Account Number', validators=[Optional(), Length(max=120)])
    mobile_number = StringField('Mobile Number', validators=[Optional(), Length(max=50)])
    bank_name = StringField('Bank Name', validators=[Optional(), Length(max=120)])
    notes = TextAreaField('Internal Notes', validators=[Optional(), Length(max=500)])
    display_order = IntegerField('Display Order', validators=[Optional(), NumberRange(min=0, max=999)], default=0)
    tenant_slug = SelectField('Scope', choices=[('', 'Global (all tenants)')], validators=[Optional()])
    qr_image = FileField(
        'QR Code Image',
        validators=[FileAllowed(['jpg', 'jpeg', 'png', 'webp'], 'Images only.')],
    )
    is_active = BooleanField('Active', default=True)
    is_default = BooleanField('Default Method', default=False)
    submit = SubmitField('Save Payment Method')


class PaymentInstructionForm(FlaskForm):
    method = SelectField(
        'Payment Method',
        choices=[
            ('PayMongo', 'PayMongo'),
        ],
        validators=[DataRequired()],
        default='PayMongo',
    )
    title = StringField('Instruction Title', validators=[DataRequired(), Length(max=120)])
    description = TextAreaField('Instruction Description', validators=[Optional(), Length(max=500)])
    account_name = StringField('Account Name', validators=[Optional(), Length(max=120)])
    account_number = StringField('Account Number / Wallet ID', validators=[Optional(), Length(max=120)])
    bank_name = StringField('Bank Name / Branch', validators=[Optional(), Length(max=120)])
    qr_image = FileField(
        'QR Code Image',
        validators=[FileAllowed(['jpg', 'jpeg', 'png', 'webp'], 'Images only.')],
    )
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Instruction')


# ── Discounts & Promotions (v6.6) ───────────────────────────────────────────

class DiscountCampaignForm(FlaskForm):
    name = StringField('Campaign Name', validators=[DataRequired(), Length(max=255)])
    code = StringField(
        'Coupon Code',
        validators=[Optional(), Length(max=100)],
        description='Leave blank for an auto-applied campaign (requires "Auto-apply" checked below).',
    )
    description = TextAreaField('Description', validators=[Optional(), Length(max=1000)])

    discount_type = SelectField(
        'Discount Type',
        choices=[('percent', 'Percentage (%)'), ('fixed', 'Fixed Amount (₱)')],
        default='percent',
        validators=[DataRequired()],
    )
    value = DecimalField(
        'Discount Value', places=2,
        validators=[DataRequired(), NumberRange(min=0.01, message='Must be greater than 0.')],
    )

    applies_to = SelectField(
        'Applies To',
        choices=[
            ('all', 'All billing cycles'),
            ('monthly', 'Monthly only'),
            ('yearly', 'Yearly only'),
            ('one_time', 'One-time (first invoice only)'),
        ],
        default='all',
        validators=[DataRequired()],
    )
    # Choices populated in the route from BILLING_PLANS so plan renames stay in sync.
    plan_slug = SelectField('Plan', choices=[('', 'All plans')], validators=[Optional()])

    is_global = BooleanField(
        'Auto-apply (no coupon code required)', default=False,
        description='If checked, this campaign applies automatically at checkout without the tenant entering a code.',
    )
    is_active = BooleanField('Active', default=True)

    usage_limit = IntegerField(
        'Total Usage Limit', validators=[Optional(), NumberRange(min=1)],
        description='Leave blank for unlimited total redemptions.',
    )
    per_tenant_limit = IntegerField(
        'Per-Tenant Limit', validators=[Optional(), NumberRange(min=1)], default=1,
        description='Leave blank for unlimited redemptions per tenant.',
    )
    first_time_only = BooleanField('First-time subscribers only', default=False)

    starts_at = DateField('Starts On', validators=[Optional()])
    expires_at = DateField('Expires On', validators=[Optional()])

    submit = SubmitField('Save Campaign')

    def validate_value(self, field):
        if self.discount_type.data == 'percent' and field.data is not None and field.data > 100:
            raise ValidationError('Percentage discount cannot exceed 100%.')

    def validate_code(self, field):
        if not self.is_global.data and not (field.data or '').strip():
            raise ValidationError('Coupon code is required unless "Auto-apply" is checked.')

    def validate_expires_at(self, field):
        if field.data and self.starts_at.data and field.data <= self.starts_at.data:
            raise ValidationError('Expiration date must be after the start date.')


class CouponApplyForm(FlaskForm):
    """Coupon code field embedded in the checkout/billing-plans form."""
    coupon_code = StringField('Coupon Code', validators=[Optional(), Length(max=100)])
    apply_coupon = SubmitField('Apply')


class SuperadminMessageForm(FlaskForm):
    tenant_slug = SelectField('Tenant', choices=[], validators=[DataRequired()])
    message_type = SelectField('Message Type', choices=[
        ('alert', 'Alert'),
        ('billing', 'Billing Update'),
        ('maintenance', 'Maintenance Notice'),
        ('account', 'Account Reminder'),
        ('general', 'General Message'),
    ], default='general')
    subject = StringField('Subject', validators=[DataRequired(), Length(max=200)])
    message = TextAreaField('Message', validators=[DataRequired(), Length(min=10, max=2000)])
    submit = SubmitField('Send Message')


class ReplyForm(FlaskForm):
    """Used by both superadmin and tenant admin to post replies."""
    message = TextAreaField('Reply', validators=[DataRequired(), Length(min=1, max=4000)])
    submit  = SubmitField('Send Reply')

# ── Skill ─────────────────────────────────────────────────────────────────────

class SkillForm(FlaskForm):
    name        = StringField('Skill Name',   validators=[DataRequired(), Length(max=100)])
    proficiency = IntegerField('Proficiency %',
                                validators=[DataRequired(), NumberRange(min=0, max=100)])
    category    = SelectField('Category', choices=[
        ('Frontend',  'Frontend'),  ('Backend',  'Backend'),
        ('Database',  'Database'),  ('DevOps',   'DevOps'),
        ('Design',    'Design'),    ('Tools',    'Tools'),
        ('Mobile',    'Mobile'),    ('Other',    'Other'),
    ])
    icon       = StringField('Icon (emoji or class)', validators=[Optional(), Length(max=100)])
    color      = StringField('Accent Color',          validators=[Optional(), Length(max=20)])
    order      = IntegerField('Display Order',         validators=[Optional(), NumberRange(min=0)],
                               default=0)
    is_visible = BooleanField('Visible on Portfolio',  default=True)
    submit     = SubmitField('Save Skill')


# ── Project ───────────────────────────────────────────────────────────────────

class ProjectForm(FlaskForm):
    title             = StringField('Project Title',
                                     validators=[DataRequired(), Length(max=200)])
    description       = TextAreaField('Full Description', validators=[Optional()])
    description_short = StringField('Short Description',
                                     validators=[Optional(), Length(max=300)])
    image             = FileField(
        'Project Image',
        validators=[FileAllowed(['jpg', 'jpeg', 'png', 'webp', 'gif'],
                                 'Images only.')]
    )
    live_url   = URLField('Live Demo URL', validators=[Optional(), URL()])
    github_url = URLField('GitHub URL',   validators=[Optional(), URL()])
    framework  = StringField('Framework / Stack',   validators=[Optional(), Length(max=120)])
    language   = StringField('Primary Language',    validators=[Optional(), Length(max=120)])
    tags       = StringField('Tags (comma-separated)', validators=[Optional()])
    category   = SelectField('Category', choices=[
        ('Web App',      'Web App'),      ('Mobile App', 'Mobile App'),
        ('API',          'API'),          ('UI/UX',      'UI/UX'),
        ('Data Science', 'Data Science'), ('DevOps',     'DevOps'),
        ('Open Source',  'Open Source'),  ('Other',      'Other'),
    ])
    status     = SelectField('Status', choices=[
        ('published', 'Published'), ('draft', 'Draft'), ('archived', 'Archived'),
    ])
    is_featured    = BooleanField('Featured on Homepage')
    date_completed = DateField('Date Completed', validators=[Optional()])
    order          = IntegerField('Display Order', validators=[Optional(), NumberRange(min=0)],
                                   default=0)
    submit = SubmitField('Save Project')


# ── Testimonial ───────────────────────────────────────────────────────────────

class TestimonialForm(FlaskForm):
    author_name    = StringField('Author Name',   validators=[DataRequired(), Length(max=100)])
    author_title   = StringField('Author Title',  validators=[Optional(), Length(max=150)])
    author_company = StringField('Company',       validators=[Optional(), Length(max=100)])
    author_avatar  = FileField(
        'Author Avatar',
        validators=[FileAllowed(['jpg', 'jpeg', 'png', 'webp'], 'Images only.')]
    )
    content    = TextAreaField('Testimonial Content', validators=[DataRequired()])
    rating     = SelectField('Rating',
                              choices=[(str(i), '⭐' * i) for i in range(1, 6)],
                              default='5', coerce=int)
    is_featured = BooleanField('Featured')
    is_visible  = BooleanField('Visible', default=True)
    order       = IntegerField('Display Order',
                                validators=[Optional(), NumberRange(min=0)], default=0)
    submit = SubmitField('Save Testimonial')


# ── Certificate ───────────────────────────────────────────────────────────────

class CertificateForm(FlaskForm):
    title             = StringField('Certification Title',
                                     validators=[DataRequired(), Length(max=255)])
    issuer            = StringField('Issuing Organization',
                                     validators=[DataRequired(), Length(max=255)])
    description       = TextAreaField('Description', validators=[Optional()])
    credential_id     = StringField('Credential ID', validators=[Optional(), Length(max=255)])
    verification_url  = URLField('Verification URL', validators=[Optional(), URL()])
    image_file        = FileField(
        'Certificate Image',
        validators=[FileAllowed(['jpg', 'jpeg', 'png', 'webp'], 'Images only.')]
    )
    badge_file        = FileField(
        'Badge Image',
        validators=[FileAllowed(['jpg', 'jpeg', 'png', 'webp'], 'Images only.')]
    )
    issue_date        = DateField('Issue Date', validators=[Optional()])
    expiration_date   = DateField('Expiration Date', validators=[Optional()])
    skills            = StringField('Skills / Tags (comma-separated)', validators=[Optional()])
    is_featured       = BooleanField('Featured')
    is_visible        = BooleanField('Visible', default=True)
    display_order     = IntegerField('Display Order',
                                      validators=[Optional(), NumberRange(min=0)], default=0)
    submit            = SubmitField('Save Certificate')


# ── Service ───────────────────────────────────────────────────────────────────

class ServiceForm(FlaskForm):
    title       = StringField('Service Title',
                               validators=[DataRequired(), Length(max=100)])
    description = TextAreaField('Description', validators=[Optional()])
    icon        = StringField('Iconify Icon',
                               validators=[Optional(), Length(max=100)],
                               description='e.g. lucide:code-2')
    features    = TextAreaField('Feature Bullets (one per line)',
                                 validators=[Optional()])
    display_order = IntegerField('Display Order',
                                  validators=[Optional(), NumberRange(min=0)],
                                  default=0)
    is_visible  = BooleanField('Visible on Portfolio', default=True)
    submit      = SubmitField('Save Service')


# ── Utilities ─────────────────────────────────────────────────────────────────

class ReorderForm(FlaskForm):
    """Hidden form used for drag-and-drop reorder POST requests."""
    order_data = HiddenField('Order Data')
    submit     = SubmitField('Save Order')


# ── Two-Factor Authentication ─────────────────────────────────────────────────

class TOTPVerifyForm(FlaskForm):
    """Used on the /admin/login/2fa verification page."""
    code = StringField(
        '6-Digit Code',
        validators=[Optional(), Length(min=6, max=6)],
    )
    backup_code = StringField(
        'Backup Code',
        validators=[Optional(), Length(min=11, max=11)],
        description='Format: XXXXX-XXXXX',
    )
    submit = SubmitField('Verify')


class TOTPSetupForm(FlaskForm):
    """Used on the /admin/profile/2fa/setup page to confirm the user scanned QR."""
    code = StringField(
        'Confirm Code from App',
        validators=[DataRequired(), Length(min=6, max=6,
                    message='Enter the 6-digit code shown in your authenticator app.')],
    )
    submit = SubmitField('Enable 2FA')


class TOTPDisableForm(FlaskForm):
    """Require current password to disable 2FA (extra safety)."""
    password = PasswordField('Current Password', validators=[DataRequired()])
    submit   = SubmitField('Disable 2FA')


class ForgotPasswordForm(FlaskForm):
    """
    Step 1 of password reset — collect username + email.

    v5.5 SECURITY FIX: previously email-only. Now requires username + email
    to match the same account (parity with tenant.auth_forgot_password),
    removing the weaker single-field identity check from the active flow.
    """
    username = StringField('Username', validators=[DataRequired()])
    email    = StringField('Email Address', validators=[DataRequired(), Email()])
    submit   = SubmitField('Send Reset Link')
