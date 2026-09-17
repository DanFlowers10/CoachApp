import os
from datetime import datetime, date

from flask import Flask, render_template, redirect, url_for, request, flash, abort
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

from models import db, User, TrainingPlan, Workout, StravaToken
import strava

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///coach.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET")
STRAVA_REDIRECT_URI = os.environ.get("STRAVA_REDIRECT_URI", "http://localhost:5000/strava/callback")

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ---------------------------------------------------------------- helpers --

def coach_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_coach():
            abort(403)
        return fn(*args, **kwargs)

    return wrapper


def client_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or current_user.is_coach():
            abort(403)
        return fn(*args, **kwargs)

    return wrapper


# -------------------------------------------------------------------- auth --

@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("coach_dashboard" if current_user.is_coach() else "client_dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    """Coach self-signup. Clients are created by their coach, not here."""
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return render_template("register.html")

        user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            role="coach",
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Welcome! Start by adding your first client.", "success")
        return redirect(url_for("coach_dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for("index"))

        flash("Incorrect email or password.", "error")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ------------------------------------------------------------ coach views --

@app.route("/coach")
@login_required
@coach_required
def coach_dashboard():
    clients = User.query.filter_by(coach_id=current_user.id).order_by(User.name).all()
    return render_template("coach_dashboard.html", clients=clients)


@app.route("/coach/clients/new", methods=["GET", "POST"])
@login_required
@coach_required
def new_client():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        temp_password = request.form["temp_password"]

        if User.query.filter_by(email=email).first():
            flash("A user with that email already exists.", "error")
            return render_template("new_client.html")

        client = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(temp_password),
            role="client",
            coach_id=current_user.id,
        )
        db.session.add(client)
        db.session.commit()
        flash(f"Client account created. Give {email} their temporary password to log in.", "success")
        return redirect(url_for("coach_dashboard"))

    return render_template("new_client.html")


@app.route("/coach/clients/<int:client_id>")
@login_required
@coach_required
def client_detail(client_id):
    client = User.query.filter_by(id=client_id, coach_id=current_user.id).first_or_404()
    plans = TrainingPlan.query.filter_by(client_id=client.id).order_by(TrainingPlan.created_at.desc()).all()
    return render_template("client_detail.html", client=client, plans=plans)


@app.route("/coach/clients/<int:client_id>/plans/new", methods=["GET", "POST"])
@login_required
@coach_required
def new_plan(client_id):
    client = User.query.filter_by(id=client_id, coach_id=current_user.id).first_or_404()

    if request.method == "POST":
        plan = TrainingPlan(
            client_id=client.id,
            coach_id=current_user.id,
            title=request.form["title"].strip(),
            goal_race=request.form.get("goal_race", "").strip(),
            notes=request.form.get("notes", "").strip(),
        )
        db.session.add(plan)
        db.session.commit()
        flash("Plan created. Now add some workouts to it.", "success")
        return redirect(url_for("plan_detail", plan_id=plan.id))

    return render_template("new_plan.html", client=client)


@app.route("/plan/<int:plan_id>")
@login_required
def plan_detail(plan_id):
    plan = db.session.get(TrainingPlan, plan_id)
    if plan is None:
        abort(404)

    # Access control: the plan's coach, or the plan's own client, may view it.
    if current_user.is_coach():
        if plan.coach_id != current_user.id:
            abort(403)
    else:
        if plan.client_id != current_user.id:
            abort(403)

    return render_template("plan_detail.html", plan=plan, today=date.today())


@app.route("/plan/<int:plan_id>/workouts/new", methods=["POST"])
@login_required
@coach_required
def new_workout(plan_id):
    plan = db.session.get(TrainingPlan, plan_id)
    if plan is None or plan.coach_id != current_user.id:
        abort(404)

    workout = Workout(
        plan_id=plan.id,
        date=datetime.strptime(request.form["date"], "%Y-%m-%d").date(),
        workout_type=request.form["workout_type"],
        target_distance_km=request.form.get("target_distance_km") or None,
        target_duration_min=request.form.get("target_duration_min") or None,
        description=request.form.get("description", "").strip(),
    )
    db.session.add(workout)
    db.session.commit()
    return redirect(url_for("plan_detail", plan_id=plan.id))


@app.route("/workout/<int:workout_id>/complete", methods=["POST"])
@login_required
@client_required
def complete_workout(workout_id):
    workout = db.session.get(Workout, workout_id)
    if workout is None or workout.plan.client_id != current_user.id:
        abort(404)

    workout.completed = True
    workout.actual_distance_km = request.form.get("actual_distance_km") or workout.target_distance_km
    workout.actual_duration_min = request.form.get("actual_duration_min") or workout.target_duration_min
    db.session.commit()
    return redirect(url_for("plan_detail", plan_id=workout.plan_id))


@app.route("/workout/<int:workout_id>/delete", methods=["POST"])
@login_required
@coach_required
def delete_workout(workout_id):
    workout = db.session.get(Workout, workout_id)
    if workout is None or workout.plan.coach_id != current_user.id:
        abort(404)
    plan_id = workout.plan_id
    db.session.delete(workout)
    db.session.commit()
    return redirect(url_for("plan_detail", plan_id=plan_id))


# ----------------------------------------------------------- client views --

@app.route("/client")
@login_required
@client_required
def client_dashboard():
    plans = TrainingPlan.query.filter_by(client_id=current_user.id).order_by(TrainingPlan.created_at.desc()).all()
    return render_template("client_dashboard.html", plans=plans)


# --------------------------------------------------------- Strava OAuth ---

@app.route("/strava/connect")
@login_required
def strava_connect():
    if not STRAVA_CLIENT_ID:
        flash("Strava isn't configured yet — add STRAVA_CLIENT_ID / SECRET to your .env file.", "error")
        return redirect(request.referrer or url_for("index"))

    url = strava.get_authorize_url(STRAVA_CLIENT_ID, STRAVA_REDIRECT_URI, state=str(current_user.id))
    return redirect(url)


@app.route("/strava/callback")
@login_required
def strava_callback():
    error = request.args.get("error")
    if error:
        flash("Strava connection was cancelled.", "error")
        return redirect(url_for("index"))

    code = request.args.get("code")
    data = strava.exchange_code_for_token(STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, code)

    token_row = current_user.strava_token
    if token_row is None:
        token_row = StravaToken(user_id=current_user.id)
        db.session.add(token_row)

    token_row.access_token = data["access_token"]
    token_row.refresh_token = data["refresh_token"]
    token_row.expires_at = data["expires_at"]
    token_row.athlete_id = str(data.get("athlete", {}).get("id", ""))
    db.session.commit()

    flash("Strava connected!", "success")
    return redirect(url_for("index"))


@app.route("/strava/activities")
@login_required
def strava_activities():
    token_row = current_user.strava_token
    if token_row is None:
        flash("Connect Strava first.", "error")
        return redirect(url_for("index"))

    access_token = strava.get_valid_access_token(token_row, STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, db)
    activities = strava.fetch_recent_activities(access_token)
    return render_template("strava_activities.html", activities=activities)


# ------------------------------------------------------------------- misc --

@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="You don't have access to that."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Not found."), 404


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)
