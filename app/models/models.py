from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import JSON, Date, ForeignKey, Index, Numeric, String, Text, Time, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

# ---- Reference value sets (validated at the Pydantic schema layer) --------
ROLES = ("member", "admin", "superadmin")
USER_STATUSES = ("active", "inactive")
DUES_STATUSES = ("unpaid", "pending", "paid")
TRANSACTION_TYPES = ("dues", "donation", "project")
TRANSACTION_STATUSES = ("pending", "success", "failed")
PROJECT_STATUSES = ("open", "closed")
BLOG_STATUSES = ("draft", "published")
CAMPAIGN_STATUSES = ("pending", "sent", "partial", "failed")
# "partial" = at least one recipient succeeded and at least one failed --
# written by app.services.campaign_sender, which is also the only writer of
# *CampaignRecipient.status below.
DELIVERY_STATUSES = ("pending", "sent", "failed")
ELECTION_STATUSES = ("draft", "open", "paused", "closed")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), default=None)
    password_hash: Mapped[str] = mapped_column(String(255))

    program: Mapped[str | None] = mapped_column(String(120), default=None)
    program_category: Mapped[str | None] = mapped_column(String(20), default=None)
    student_id: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, default=None)
    grad_year: Mapped[int | None] = mapped_column(default=None)

    # Manual corrections for cases the student_id/program_category formula
    # can't derive on its own — e.g. a diploma graduate continuing straight
    # into a degree program at level 200, or a student rusticated for a year.
    # When set, these win over the computed level/graduation year everywhere
    # (see app.services.academic.effective_level / effective_grad_year).
    level_override: Mapped[int | None] = mapped_column(default=None)
    grad_year_override: Mapped[int | None] = mapped_column(default=None)
    academic_override_note: Mapped[str | None] = mapped_column(Text, default=None)

    role: Mapped[str] = mapped_column(String(20), default="member")
    status: Mapped[str] = mapped_column(String(20), default="active")
    member_since: Mapped[date] = mapped_column(Date, default=date.today)
    title: Mapped[str | None] = mapped_column(String(120), default=None)

    sms_opt_in: Mapped[bool] = mapped_column(default=True)
    email_opt_in: Mapped[bool] = mapped_column(default=True)
    must_change_password: Mapped[bool] = mapped_column(default=False)
    profile_picture_url: Mapped[str | None] = mapped_column(String(500), default=None)

    dues_records: Mapped[list["DuesRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", foreign_keys="DuesRecord.user_id"
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", foreign_keys="Transaction.user_id"
    )
    rsvps: Mapped[list["Rsvp"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def initials(self) -> str:
        parts = [p for p in self.name.split() if p]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(80))
    icon: Mapped[str] = mapped_column(String(60), default="layout-grid")
    summary: Mapped[str] = mapped_column(Text)
    target: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    current: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="GHS")
    status: Mapped[str] = mapped_column(String(20), default="open")
    deadline: Mapped[date | None] = mapped_column(Date, default=None)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="project")

    @property
    def progress_percent(self) -> int:
        if not self.target:
            return 0
        return min(100, round(float(self.current) / float(self.target) * 100))


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, default=None)
    donor_name: Mapped[str | None] = mapped_column(String(160), default=None)
    donor_email: Mapped[str | None] = mapped_column(String(255), default=None)

    type: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="GHS")
    method: Mapped[str | None] = mapped_column(String(80), default=None)
    reference: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True, default=None)
    paystack_data: Mapped[dict | None] = mapped_column(JSON, default=None)

    proof_url: Mapped[str | None] = mapped_column(String(500), default=None)
    verified_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    verified_at: Mapped[datetime | None] = mapped_column(default=None)

    user: Mapped["User | None"] = relationship(
        back_populates="transactions", foreign_keys=[user_id]
    )
    project: Mapped["Project | None"] = relationship(back_populates="transactions")
    verified_by: Mapped["User | None"] = relationship(foreign_keys=[verified_by_id])


class DuesRecord(Base, TimestampMixin):
    __tablename__ = "dues_records"
    __table_args__ = (UniqueConstraint("user_id", "semester", name="uq_dues_user_semester"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    semester: Mapped[str] = mapped_column(String(40))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="GHS")
    status: Mapped[str] = mapped_column(String(20), default="unpaid", index=True)
    due_date: Mapped[date | None] = mapped_column(Date, default=None)
    paid_at: Mapped[datetime | None] = mapped_column(default=None)
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id"), default=None
    )

    user: Mapped["User"] = relationship(back_populates="dues_records", foreign_keys=[user_id])


class Expense(Base, TimestampMixin):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(80))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="GHS")
    recorded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    receipt_url: Mapped[str | None] = mapped_column(String(500), default=None)

    recorded_by: Mapped["User | None"] = relationship()


class Event(Base, TimestampMixin):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(80))
    date: Mapped[date] = mapped_column(Date)
    time: Mapped[str] = mapped_column(String(8))
    end_time: Mapped[str | None] = mapped_column(String(8), default=None)
    location: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    is_public: Mapped[bool] = mapped_column(default=True)
    capacity: Mapped[int | None] = mapped_column(default=None)
    rsvp_required: Mapped[bool] = mapped_column(default=False)
    icon: Mapped[str] = mapped_column(String(60), default="calendar")

    rsvps: Mapped[list["Rsvp"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class Rsvp(Base, TimestampMixin):
    __tablename__ = "rsvps"
    __table_args__ = (UniqueConstraint("user_id", "event_id", name="uq_rsvp_user_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)

    user: Mapped["User"] = relationship(back_populates="rsvps")
    event: Mapped["Event"] = relationship(back_populates="rsvps")


class BlogPost(Base, TimestampMixin):
    __tablename__ = "blog_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    excerpt: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(80))
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    author_name: Mapped[str] = mapped_column(String(120))
    author_role: Mapped[str | None] = mapped_column(String(120), default=None)
    date: Mapped[date] = mapped_column(Date, default=date.today)
    read_time: Mapped[int] = mapped_column(default=1)
    icon: Mapped[str] = mapped_column(String(60), default="book-open")
    content: Mapped[list[str]] = mapped_column(JSON, default=list)
    content_ar: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    image_url: Mapped[str | None] = mapped_column(String(500), default=None)
    status: Mapped[str] = mapped_column(String(20), default="published")

    author: Mapped["User | None"] = relationship()


class Announcement(Base, TimestampMixin):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    date: Mapped[date] = mapped_column(Date, default=date.today)
    audience: Mapped[str] = mapped_column(String(120), default="All Members")
    link_url: Mapped[str | None] = mapped_column(String(500), default=None)


class LeadershipBoard(Base, TimestampMixin):
    __tablename__ = "leadership_boards"

    id: Mapped[int] = mapped_column(primary_key=True)
    term: Mapped[str] = mapped_column(String(80))
    is_current: Mapped[bool] = mapped_column(default=False)

    members: Mapped[list["LeadershipMember"]] = relationship(
        back_populates="board", cascade="all, delete-orphan", order_by="LeadershipMember.order_index"
    )
    committees: Mapped[list["Committee"]] = relationship(
        back_populates="board", cascade="all, delete-orphan", order_by="Committee.order_index"
    )


class LeadershipMember(Base, TimestampMixin):
    __tablename__ = "leadership_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("leadership_boards.id"))
    # Optional link to a real account, set when this entry was created via the
    # "Assign Role" flow on the Members page rather than typed in by hand.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(32), default=None)
    initials: Mapped[str | None] = mapped_column(String(8), default=None)
    photo_url: Mapped[str | None] = mapped_column(String(500), default=None)
    order_index: Mapped[int] = mapped_column(default=0)

    board: Mapped["LeadershipBoard"] = relationship(back_populates="members")
    user: Mapped["User | None"] = relationship()


class Committee(Base, TimestampMixin):
    __tablename__ = "committees"

    id: Mapped[int] = mapped_column(primary_key=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("leadership_boards.id"))
    name: Mapped[str] = mapped_column(String(160))
    order_index: Mapped[int] = mapped_column(default=0)

    board: Mapped["LeadershipBoard"] = relationship(back_populates="committees")
    members: Mapped[list["CommitteeMember"]] = relationship(
        back_populates="committee", cascade="all, delete-orphan", order_by="CommitteeMember.order_index"
    )


class CommitteeMember(Base, TimestampMixin):
    __tablename__ = "committee_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    committee_id: Mapped[int] = mapped_column(ForeignKey("committees.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(32), default=None)
    order_index: Mapped[int] = mapped_column(default=0)

    committee: Mapped["Committee"] = relationship(back_populates="members")
    user: Mapped["User | None"] = relationship()


class Resource(Base, TimestampMixin):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(20))
    size: Mapped[str | None] = mapped_column(String(20), default=None)
    date: Mapped[date] = mapped_column(Date, default=date.today)
    description: Mapped[str] = mapped_column(Text)
    file_url: Mapped[str] = mapped_column(String(500))


class SmsCampaign(Base, TimestampMixin):
    __tablename__ = "sms_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    audience: Mapped[str] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(Text)
    characters: Mapped[int] = mapped_column(default=0)
    segments: Mapped[int] = mapped_column(default=1)
    recipients_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    sent_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)

    sent_by: Mapped["User | None"] = relationship()
    recipients: Mapped[list["SmsCampaignRecipient"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class SmsCampaignRecipient(Base, TimestampMixin):
    """One row per recipient of an SmsCampaign, written by
    app.services.campaign_sender as it works through the audience in chunks
    -- makes a partial failure (e.g. one batch of 300 rejected by Arkesel)
    visible per-recipient instead of collapsing into SmsCampaign's single
    aggregate `status`. Written per-batch, not per-recipient, since Arkesel
    sends are chunked API calls, not one call per recipient (see
    campaign_sender for why that split is safe for SMS but not for email).
    """

    __tablename__ = "sms_campaign_recipients"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("sms_campaigns.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    phone: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(Text, default=None)
    sent_at: Mapped[datetime | None] = mapped_column(default=None)

    campaign: Mapped["SmsCampaign"] = relationship(back_populates="recipients")
    user: Mapped["User | None"] = relationship()


class EmailCampaign(Base, TimestampMixin):
    __tablename__ = "email_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    audience: Mapped[str] = mapped_column(String(160))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    recipients_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # Reserved for future webhook-based open tracking (Resend/Brevo/SES all
    # support delivery/open webhooks, but none are wired up yet). Nothing in
    # this codebase populates this column today -- intentionally left as a
    # placeholder for that future work rather than removed, since the schema
    # support costs nothing and re-adding it later would need a new
    # migration anyway. Always None until that webhook handler exists.
    open_rate: Mapped[float | None] = mapped_column(default=None)
    sent_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)

    sent_by: Mapped["User | None"] = relationship()
    recipients: Mapped[list["EmailCampaignRecipient"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class EmailCampaignRecipient(Base, TimestampMixin):
    """One row per recipient of an EmailCampaign, written by
    app.services.campaign_sender as it sends one-by-one -- makes a partial
    failure (e.g. 40 of 100 recipients bouncing) visible per-recipient
    instead of collapsing into EmailCampaign's single aggregate `status`.
    """

    __tablename__ = "email_campaign_recipients"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("email_campaigns.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    email: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(Text, default=None)
    sent_at: Mapped[datetime | None] = mapped_column(default=None)

    campaign: Mapped["EmailCampaign"] = relationship(back_populates="recipients")
    user: Mapped["User | None"] = relationship()


class AppSecret(Base, TimestampMixin):
    """Encrypted API keys/secrets managed only via the superadmin dashboard.

    encrypted_value holds a Fernet ciphertext (see app.services.secrets_store);
    last4 is kept in plaintext purely so the admin UI can show a masked hint
    ("...wXyz") without ever decrypting for display.
    """

    __tablename__ = "app_secrets"

    id: Mapped[int] = mapped_column(primary_key=True)
    key_name: Mapped[str] = mapped_column(String(60), unique=True)
    encrypted_value: Mapped[str] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(String(8))
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)

    updated_by: Mapped["User | None"] = relationship()


class OrgSettings(Base, TimestampMixin):
    __tablename__ = "org_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(120), default="GMSA UTAS")
    full_name: Mapped[str] = mapped_column(
        String(255), default="GMSA — University of Technology and Applied Sciences"
    )
    tagline: Mapped[str] = mapped_column(String(255), default="Faith, Community, Excellence.")
    email: Mapped[str] = mapped_column(String(255), default="info@gmsautas.org")
    phone: Mapped[str] = mapped_column(String(32), default="+233 50 000 0000")
    address: Mapped[str] = mapped_column(
        String(255), default="Student Centre, Level 2, Room 204, UTAS Campus"
    )
    social: Mapped[dict] = mapped_column(JSON, default=dict)
    founding_year: Mapped[int] = mapped_column(default=2009)
    momo_number: Mapped[str | None] = mapped_column(String(32), default=None)
    momo_name: Mapped[str | None] = mapped_column(String(160), default=None)
    bank_name: Mapped[str | None] = mapped_column(String(160), default=None)
    bank_account_name: Mapped[str | None] = mapped_column(String(160), default=None)
    bank_account_number: Mapped[str | None] = mapped_column(String(64), default=None)

    # DB-backed overrides for operational config that previously required an
    # env var + redeploy to change (see app.services.org_settings_cache).
    # None means "fall back to the Settings env var default" — leaving these
    # untouched preserves today's behavior exactly.
    # NOTE: resend_from_email/ses_from_email/ses_region columns still exist in
    # the DB (added by b2c3d4e5f6a7, already deployed) but are deliberately
    # unmapped here now that Resend/SES support has been removed from
    # app.services.resend_client -- dropping live columns nothing reads isn't
    # worth a destructive migration on production for a no-op cleanup.
    email_provider: Mapped[str | None] = mapped_column(String(20), default=None)
    brevo_from_email: Mapped[str | None] = mapped_column(String(255), default=None)
    arkesel_sender_id: Mapped[str | None] = mapped_column(String(20), default=None)

    # Per-semester dues amounts, GHS. None means "fall back to the Settings
    # env var default" (see app.services.academic).
    dues_amount_ghs: Mapped[int | None] = mapped_column(default=None)
    dues_amount_level_100: Mapped[int | None] = mapped_column(default=None)
    dues_amount_continuing: Mapped[int | None] = mapped_column(default=None)
    dues_amount_final_year: Mapped[int | None] = mapped_column(default=None)


class PrayerTimes(Base, TimestampMixin):
    """Today's prayer times as manually entered/edited by an admin in the
    Content module — deliberately not fetched from a third-party API, since
    that pulled in a hard dependency on Aladhan being reachable to show the
    home page's prayer widget. A single row (id=1), same singleton pattern
    as OrgSettings; the admin is expected to update it periodically.
    """

    __tablename__ = "prayer_times"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    fajr: Mapped[time | None] = mapped_column(Time, default=None)
    sunrise: Mapped[time | None] = mapped_column(Time, default=None)
    dhuhr: Mapped[time | None] = mapped_column(Time, default=None)
    asr: Mapped[time | None] = mapped_column(Time, default=None)
    maghrib: Mapped[time | None] = mapped_column(Time, default=None)
    isha: Mapped[time | None] = mapped_column(Time, default=None)
    location_label: Mapped[str | None] = mapped_column(String(120), default=None)


class PageContentBlock(Base, TimestampMixin):
    """A single named eyebrow/heading/body slot of marketing copy on the
    public home or About page — e.g. the hero text, the mission statement.
    Edited by an admin under Content > Page Content; templates fall back to
    the original hardcoded copy if a key is somehow missing (shouldn't
    happen once seeded, but avoids a blank page if it is).
    """

    __tablename__ = "page_content_blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    eyebrow: Mapped[str | None] = mapped_column(String(120), default=None)
    heading: Mapped[str | None] = mapped_column(String(255), default=None)
    body: Mapped[str | None] = mapped_column(Text, default=None)
    # Small structured extras a block might need (e.g. a short bullet list) —
    # shape is block-specific; see each template's usage.
    extra: Mapped[dict | None] = mapped_column(JSON, default=None)


class AboutPillar(Base, TimestampMixin):
    """One "What We Do" focus-area card on the About page."""

    __tablename__ = "about_pillars"

    id: Mapped[int] = mapped_column(primary_key=True)
    icon: Mapped[str] = mapped_column(String(60), default="check-circle-2")
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    order_index: Mapped[int] = mapped_column(default=0)


class Milestone(Base, TimestampMixin):
    """One dated entry in the About page's "Our Journey" timeline."""

    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    order_index: Mapped[int] = mapped_column(default=0)


class Election(Base, TimestampMixin):
    __tablename__ = "elections"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    starts_at: Mapped[datetime] = mapped_column()
    ends_at: Mapped[datetime] = mapped_column()
    auto_publish: Mapped[bool] = mapped_column(default=True)
    # A sandbox election used to rehearse the full flow (positions, candidates,
    # voters, votes) without counting against the one-election-per-year limit
    # and without the usual draft-only deletion restriction -- see
    # app.services.elections.assert_year_available / assert_election_deletable.
    is_test: Mapped[bool] = mapped_column(default=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)

    created_by: Mapped["User | None"] = relationship()
    positions: Mapped[list["Position"]] = relationship(
        back_populates="election", cascade="all, delete-orphan", order_by="Position.order_index"
    )
    voters: Mapped[list["Voter"]] = relationship(
        back_populates="election", cascade="all, delete-orphan"
    )
    # Votes reference election/position/candidate/voter/voter_token, none of
    # which have an ON DELETE CASCADE at the DB level -- without this
    # relationship-level cascade, deleting an election that already has votes
    # (allowed for is_test elections regardless of status) would hit an FK
    # constraint when the ORM then tries to cascade-delete positions/voters.
    votes: Mapped[list["Vote"]] = relationship(
        back_populates="election", cascade="all, delete-orphan"
    )


class Position(Base, TimestampMixin):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("election_id", "title", name="uq_position_election_title"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    election_id: Mapped[int] = mapped_column(ForeignKey("elections.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    order_index: Mapped[int] = mapped_column(default=0)

    election: Mapped["Election"] = relationship(back_populates="positions")
    candidates: Mapped[list["Candidate"]] = relationship(
        back_populates="position", cascade="all, delete-orphan", order_by="Candidate.order_index"
    )


class Candidate(Base, TimestampMixin):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, default=None)
    name: Mapped[str] = mapped_column(String(160))
    bio: Mapped[str | None] = mapped_column(Text, default=None)
    photo_url: Mapped[str | None] = mapped_column(String(500), default=None)
    order_index: Mapped[int] = mapped_column(default=0)

    position: Mapped["Position"] = relationship(back_populates="candidates")
    user: Mapped["User | None"] = relationship()


class Voter(Base, TimestampMixin):
    __tablename__ = "voters"
    __table_args__ = (UniqueConstraint("election_id", "user_id", name="uq_voter_election_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    election_id: Mapped[int] = mapped_column(ForeignKey("elections.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    has_voted: Mapped[bool] = mapped_column(default=False)

    election: Mapped["Election"] = relationship(back_populates="voters")
    user: Mapped["User"] = relationship()
    tokens: Mapped[list["VoterToken"]] = relationship(
        back_populates="voter", cascade="all, delete-orphan"
    )


class PasswordResetToken(Base, TimestampMixin):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column()
    used_at: Mapped[datetime | None] = mapped_column(default=None)

    user: Mapped["User"] = relationship()


class VoterToken(Base, TimestampMixin):
    __tablename__ = "voter_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    voter_id: Mapped[int] = mapped_column(ForeignKey("voters.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    is_used: Mapped[bool] = mapped_column(default=False)
    used_at: Mapped[datetime | None] = mapped_column(default=None)
    is_nullified: Mapped[bool] = mapped_column(default=False)

    voter: Mapped["Voter"] = relationship(back_populates="tokens")


class Vote(Base, TimestampMixin):
    __tablename__ = "votes"
    __table_args__ = (
        # Backstop for the application-level single-vote guarantee: at most one
        # active (non-nullified) vote per voter per position.
        Index(
            "uq_active_vote_voter_position",
            "voter_id",
            "position_id",
            unique=True,
            postgresql_where=text("is_nullified = false"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    election_id: Mapped[int] = mapped_column(ForeignKey("elections.id"), index=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    voter_id: Mapped[int] = mapped_column(ForeignKey("voters.id"), index=True)
    voter_token_id: Mapped[int] = mapped_column(ForeignKey("voter_tokens.id"), index=True)

    # Only meaningful when the position was uncontested (one candidate): the
    # voter is choosing to elect (False) or reject (True) that sole candidate,
    # rather than picking among rivals. Always False for contested positions.
    is_no: Mapped[bool] = mapped_column(default=False)

    is_nullified: Mapped[bool] = mapped_column(default=False)
    nullified_at: Mapped[datetime | None] = mapped_column(default=None)
    nullified_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    nullify_reason: Mapped[str | None] = mapped_column(Text, default=None)

    election: Mapped["Election"] = relationship(back_populates="votes")
    position: Mapped["Position"] = relationship()
    candidate: Mapped["Candidate"] = relationship()
    voter: Mapped["Voter"] = relationship()
    voter_token: Mapped["VoterToken"] = relationship()
    nullified_by: Mapped["User | None"] = relationship(foreign_keys=[nullified_by_id])


class EmailSendFailure(Base, TimestampMixin):
    """A record of every outbound email that failed to send (a caught
    resend_client.ResendError), regardless of whether the caller itself
    swallows the exception (e.g. password_reset.request_reset's
    anti-enumeration behavior) or re-raises it. Pure observability -- this
    table is only ever read from the superadmin "Email Failures" panel, and
    nothing in the app behaves differently because a row exists here. See
    app.services.email_failures.record_failure, the single write path.
    """

    __tablename__ = "email_send_failures"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    recipient: Mapped[str] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(120))
    error: Mapped[str] = mapped_column(Text)
