from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


class User(db.Model, UserMixin):
    """A coach OR a client. Role decides what they can do."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'coach' or 'client'

    # Only set for clients: which coach manages them
    coach_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    clients = db.relationship(
        "User", backref=db.backref("coach", remote_side=[id])
    )

    plans = db.relationship(
        "TrainingPlan",
        backref="client",
        lazy=True,
        foreign_keys="TrainingPlan.client_id",
        cascade="all, delete-orphan",
    )

    strava_token = db.relationship(
        "StravaToken", backref="user", uselist=False, cascade="all, delete-orphan"
    )

    def is_coach(self):
        return self.role == "coach"


class TrainingPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    coach_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    goal_race = db.Column(db.String(200))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    workouts = db.relationship(
        "Workout",
        backref="plan",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="Workout.date",
    )


class Workout(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("training_plan.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    workout_type = db.Column(db.String(50), nullable=False)  # Easy, Long, Tempo, Interval, Rest, Race...
    # Despite the column name, the UI now displays/collects these in miles, not km
    # (kept as-is to avoid a production schema change since Render's table already exists).
    target_distance_km = db.Column(db.Float)
    target_duration_min = db.Column(db.Integer)
    description = db.Column(db.Text)

    completed = db.Column(db.Boolean, default=False)
    actual_distance_km = db.Column(db.Float)
    actual_duration_min = db.Column(db.Integer)
    strava_activity_id = db.Column(db.String(50))  # set automatically if matched to a Strava activity


class StravaToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    access_token = db.Column(db.String(255))
    refresh_token = db.Column(db.String(255))
    expires_at = db.Column(db.Integer)  # unix timestamp
    athlete_id = db.Column(db.String(50))
