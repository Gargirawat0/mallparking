import os
import random
import string
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from twilio.rest import Client
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///mall_parking.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

TWILIO_ACCOUNT_SID = 'ACe361b948aa2e743cf4147a86aadc5298'
TWILIO_AUTH_TOKEN = 'faa1c728f0d592afe8a1e398450811df'
TWILIO_PHONE_NUMBER = '+18624538645'
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(15), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    vehicles = db.relationship('Vehicle', backref='owner', lazy=True)
    is_admin = db.Column(db.Boolean, default=False)
    bookings = db.relationship('Booking', backref='user', lazy=True)

class Vehicle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(20), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Slot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    floor = db.Column(db.Integer, nullable=False)
    number = db.Column(db.Integer, nullable=False)
    is_available = db.Column(db.Boolean, default=True)
    bookings = db.relationship('Booking', backref='slot', lazy=True)

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey('slot.id'), nullable=False)
    vehicle_number = db.Column(db.String(20), nullable=False)
    booking_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    duration = db.Column(db.Integer, nullable=False) 
    end_time = db.Column(db.Time, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='upcoming')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    payment_status = db.Column(db.Boolean, default=False)

with app.app_context():
    db.create_all()
    
    if Slot.query.count() == 0:
        for floor in [1, 2]:
            for num in range(1, 26):
                slot = Slot(floor=floor, number=num, is_available=True)
                db.session.add(slot)
        db.session.commit()

    if not User.query.filter_by(is_admin=True).first():
        admin = User(
            phone='9999999999',
            password='admin@123',
            name='Admin',
            is_admin=True
        )
        db.session.add(admin)
        db.session.commit()

def update_booking_status():
    with app.app_context():
        now = datetime.now()
        upcoming_bookings = Booking.query.filter_by(status='upcoming').all()
        
        for booking in upcoming_bookings:
            booking_datetime = datetime.combine(booking.booking_date, booking.end_time)
            if now >= booking_datetime:
                booking.status = 'completed'
                slot = Slot.query.get(booking.slot_id)
                slot.is_available = True
                db.session.commit()

scheduler = BackgroundScheduler(timezone=pytz.utc)
scheduler.add_job(func=update_booking_status, trigger="interval", minutes=1)
scheduler.start()

def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

def validate_vehicle_number(number):
    if len(number) < 9 or len(number) > 13:
        return False
    return True

def validate_password(password):
    if (len(password) >= 8 and 
        any(c.isupper() for c in password) and 
        any(c.islower() for c in password) and 
        any(c.isdigit() for c in password) and 
        any(not c.isalnum() for c in password)):
        return True
    return False

def calculate_amount(duration):
    return duration * 50

@app.route('/')
def home():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        phone = request.form['phone']
        name = request.form['name']
        password = request.form['password']
        vehicle_number = request.form['vehicle_number']

        if not validate_password(password):
            flash('Password must be at least 8 characters with uppercase, lowercase, number, and special character', 'danger')
            return redirect(url_for('register'))
        
        if not validate_vehicle_number(vehicle_number):
            flash('Invalid vehicle number format', 'danger')
            return redirect(url_for('register'))
        
        if User.query.filter_by(phone=phone).first():
            flash('Phone number already registered', 'danger')
            return redirect(url_for('register'))
        
        if Vehicle.query.filter_by(number=vehicle_number).first():
            flash('Vehicle number already registered with another user', 'danger')
            return redirect(url_for('register'))

        session['registration_data'] = {
            'phone': phone,
            'name': name,
            'password': password,
            'vehicle_number': vehicle_number
        }

        otp = generate_otp()
        session['otp'] = otp
        session['otp_phone'] = phone

        try:
             message = client.messages.create(
                 body=f'Your OTP for Mall Parking registration is: {otp}',
                 from_=TWILIO_PHONE_NUMBER,
                 to=f'+91{phone}'
             )
        except Exception as e:
             flash('Failed to send OTP. Please try again.', 'danger')
             return redirect(url_for('register'))
        
        flash(f'OTP sent to {phone} (Demo OTP: {otp})', 'info')
        return redirect(url_for('verify_otp'))
    
    return render_template('register.html')

@app.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    if 'registration_data' not in session:
        return redirect(url_for('register'))
    
    if request.method == 'POST':
        user_otp = request.form['otp']
        
        if user_otp == session.get('otp') and session.get('otp_phone') == session['registration_data']['phone']:
            user_data = session['registration_data']
            user = User(
                phone=user_data['phone'],
                name=user_data['name'],
                password=user_data['password']
            )
            db.session.add(user)
            db.session.commit()

            vehicle = Vehicle(number=user_data['vehicle_number'], user_id=user.id)
            db.session.add(vehicle)
            db.session.commit()

            session.pop('registration_data', None)
            session.pop('otp', None)
            session.pop('otp_phone', None)

            login_user(user)
            flash('Registration successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid OTP', 'danger')
    
    return render_template('verify_otp.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        phone = request.form['phone']
        password = request.form['password']
        user = User.query.filter_by(phone=phone).first()
        
        if user and user.password == password:
            login_user(user)
            next_page = request.args.get('next')
            flash('Login successful!', 'success')
            return redirect(next_page or url_for('dashboard'))
        else:
            flash('Login failed. Check phone number and password.', 'danger')
    
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_authenticated and current_user.is_admin:
        return redirect(url_for('admin_dashboard'))

    active_bookings = Booking.query.filter(
        Booking.user_id == current_user.id,
        Booking.status == 'upcoming'
    ).order_by(Booking.booking_date, Booking.start_time).all()

    history_bookings = Booking.query.filter(
        Booking.user_id == current_user.id,
        Booking.status.in_(['completed', 'cancelled'])
    ).order_by(Booking.booking_date.desc(), Booking.start_time.desc()).limit(10).all()
    
    return render_template('dashboard.html', 
                         active_bookings=active_bookings, 
                         history_bookings=history_bookings)

@app.route('/book-slot', methods=['GET', 'POST'])
@login_required
def book_slot():
    if request.method == 'POST':
        slot_id = request.form['slot_id']
        vehicle_number = request.form['vehicle_number']
        booking_date = request.form['booking_date']
        start_time = request.form['start_time']
        duration = int(request.form['duration'])

        if not validate_vehicle_number(vehicle_number):
            flash('Invalid vehicle number format', 'danger')
            return redirect(url_for('book_slot'))

        if not Vehicle.query.filter_by(number=vehicle_number, user_id=current_user.id).first():
            flash('This vehicle is not registered with your account', 'danger')
            return redirect(url_for('book_slot'))
 
        existing_booking = Booking.query.filter(
            Booking.user_id == current_user.id,
            Booking.vehicle_number == vehicle_number,
            Booking.status == 'upcoming'
        ).first()
        
        if existing_booking:
            flash('You already have an active booking with this vehicle', 'danger')
            return redirect(url_for('book_slot'))

        try:
            booking_date = datetime.strptime(booking_date, '%Y-%m-%d').date()
            start_time = datetime.strptime(start_time, '%H:%M').time()
        except ValueError:
            flash('Invalid date or time format', 'danger')
            return redirect(url_for('book_slot'))

        start_datetime = datetime.combine(booking_date, start_time)

        # Check if the selected time has already passed
        if start_datetime < datetime.now():
            flash('Selected time has already passed. Please choose a future time.', 'danger')
            return redirect(url_for('book_slot'))

        end_datetime = start_datetime + timedelta(hours=duration)
        end_time = end_datetime.time()

        slot = Slot.query.get(slot_id)
        if not slot or not slot.is_available:
            flash('Selected slot is not available', 'danger')
            return redirect(url_for('book_slot'))

        conflicting_booking = Booking.query.filter(
            Booking.slot_id == slot_id,
            Booking.booking_date == booking_date,
            Booking.status == 'upcoming',
            ((Booking.start_time <= start_time) & (Booking.end_time > start_time)) |
            ((Booking.start_time < end_time) & (Booking.end_time >= end_time)) |
            ((Booking.start_time >= start_time) & (Booking.end_time <= end_time))
        ).first()
        
        if conflicting_booking:
            flash('This slot is already booked for the selected time', 'danger')
            return redirect(url_for('book_slot'))
        
        amount = calculate_amount(duration)

        booking = Booking(
            user_id=current_user.id,
            slot_id=slot_id,
            vehicle_number=vehicle_number,
            booking_date=booking_date,
            start_time=start_time,
            duration=duration,
            end_time=end_time,
            amount=amount,
            payment_status=True 
        )

        slot.is_available = False
        
        db.session.add(booking)
        db.session.commit()

        try:
             message = client.messages.create(
                 body=f'🚗Your parking slot {slot.floor}-{slot.number} is booked for {booking_date} {start_time} to {end_time}. Amount: ₹{amount}',
                 from_=TWILIO_PHONE_NUMBER,
                 to=f'+91{current_user.phone}'
             )
        except Exception as e:
             print(f"Failed to send SMS: {e}")
        
        flash(f'🚗 Slot booked successfully!  Amount: ₹{amount}', 'success')
        return redirect(url_for('dashboard'))
    
    floor1_slots = Slot.query.filter_by(floor=1).order_by(Slot.number).all()
    floor2_slots = Slot.query.filter_by(floor=2).order_by(Slot.number).all()
    
    vehicles = Vehicle.query.filter_by(user_id=current_user.id).all()
    
    return render_template('book_slot.html', 
                         floor1_slots=floor1_slots, 
                         floor2_slots=floor2_slots,
                         vehicles=vehicles,
                         min_date=datetime.now().strftime('%Y-%m-%d'))


@app.route('/cancel-booking/<int:booking_id>', methods=['POST'])
@login_required
def cancel_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    
    if booking.user_id != current_user.id and not current_user.is_admin:
        flash('❌You are not authorized to cancel this booking', 'danger')
        return redirect(url_for('dashboard'))

    slot = Slot.query.get(booking.slot_id)
    slot.is_available = True

    booking.status = 'cancelled'
    db.session.commit()

    try:
         message = client.messages.create(
             body=f'❌Your parking booking for slot {slot.floor}-{slot.number} on {booking.booking_date} has been cancelled.',
             from_=TWILIO_PHONE_NUMBER,
             to=f'+91{current_user.phone}'
         )
    except Exception as e:
         print(f"Failed to send SMS: {e}")
    
    flash('Booking cancelled successfully', 'success')
    return redirect(url_for('dashboard'))

@app.route('/add-vehicle', methods=['GET', 'POST'])
@login_required
def add_vehicle():
    if request.method == 'POST':
        vehicle_number = request.form['vehicle_number']
        
        if not validate_vehicle_number(vehicle_number):
            flash('Invalid vehicle number format', 'danger')
            return redirect(url_for('add_vehicle'))
        
        if Vehicle.query.filter_by(number=vehicle_number).first():
            flash('Vehicle number already registered', 'danger')
            return redirect(url_for('add_vehicle'))
        
        vehicle = Vehicle(number=vehicle_number, user_id=current_user.id)
        db.session.add(vehicle)
        db.session.commit()
        
        flash('Vehicle added successfully', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('add_vehicle.html')

@app.route('/remove-vehicle/<int:vehicle_id>', methods=['POST'])
@login_required
def remove_vehicle(vehicle_id):
    vehicle = Vehicle.query.get_or_404(vehicle_id)
    
    if vehicle.user_id != current_user.id:
        flash('You are not authorized to remove this vehicle', 'danger')
        return redirect(url_for('dashboard'))

    active_booking = Booking.query.filter(
        Booking.vehicle_number == vehicle.number,
        Booking.status == 'upcoming',
        Booking.user_id == current_user.id
    ).first()
    
    if active_booking:
        flash('Cannot remove vehicle with active bookings', 'danger')
        return redirect(url_for('dashboard'))
    
    db.session.delete(vehicle)
    db.session.commit()
    
    flash('Vehicle removed successfully', 'success')
    return redirect(url_for('dashboard'))

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        return redirect(url_for('dashboard'))
    
    try:
        upcoming_bookings = Booking.query.filter_by(status='upcoming').order_by(
            Booking.booking_date, Booking.start_time
        ).all()
        
        recent_history = Booking.query.filter(
            Booking.status.in_(['completed', 'cancelled'])
        ).order_by(Booking.booking_date.desc(), Booking.start_time.desc()).limit(20).all()
        
        users = User.query.filter_by(is_admin=False).order_by(User.name).all()
        
        return render_template('admin_dashboard.html', 
                            upcoming_bookings=upcoming_bookings,
                            recent_history=recent_history,
                            users=users)
    except Exception as e:
        return render_template('admin_dashboard.html', 
                            error=str(e))
    
@app.route('/admin/user/<int:user_id>/bookings')
@login_required
def admin_view_user_bookings(user_id):
    if not current_user.is_admin:
        return redirect(url_for('dashboard'))
    
    user = User.query.get_or_404(user_id)
    bookings = Booking.query.filter_by(user_id=user.id).order_by(
        Booking.booking_date.desc(), Booking.start_time.desc()
    ).all()
    
    return render_template('admin_user_bookings.html', user=user, bookings=bookings)


@app.route('/admin/cancel-booking/<int:booking_id>', methods=['POST'])
@login_required
def admin_cancel_booking(booking_id):
    if not current_user.is_admin:
        return redirect(url_for('dashboard'))
    
    booking = Booking.query.get_or_404(booking_id)

    slot = Slot.query.get(booking.slot_id)
    slot.is_available = True

    booking.status = 'cancelled'
    db.session.commit()

    try:
        user = User.query.get(booking.user_id)
        message = client.messages.create(
             body=f'❌Admin has cancelled your parking booking for slot {slot.floor}-{slot.number} on {booking.booking_date}.',
             from_=TWILIO_PHONE_NUMBER,
             to=f'+91{user.phone}'
         )
    except Exception as e:
         print(f"Failed to send SMS: {e}")
    
    flash('Booking cancelled successfully', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out', 'info')
    return redirect(url_for('home'))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))