import pigpio
import time

# Inputs class
# Functionality for rotary encoder and button
class Inputs:
    # Initialize gpio pin values and callbacks for rotation and button press
    # Must pass rotation_callback and button_callback in main for new encoder object
    # Must create methods in main to handle the callbacks
    # Long press callback takes care of 3 second button press
    def __init__(self, pi, clk, dt, sw, rotation_callback, button_callback, long_press_callback):
        self.pi = pi
        self.clk = clk
        self.dt = dt
        self.sw = sw
        self.last_tick = 0
        self.rotate = rotation_callback
        self.click = button_callback
        self.long_press = long_press_callback
        self.button_press_start = 0
        self.button_pressed = False
        self.long_press_triggered = False

        self.setup_gpio()

    # Sets each component to input
    # Sets each component as pull up resistor
    def setup_gpio(self):
        for gpio in [self.clk, self.dt, self.sw]:
            self.pi.set_mode(gpio, pigpio.INPUT)
            self.pi.set_pull_up_down(gpio, pigpio.PUD_UP)
            
        self.last_dt_state = self.pi.read(self.dt)   

        self.pi.callback(self.dt, pigpio.EITHER_EDGE, self.dt_changed)
        self.pi.callback(self.clk, pigpio.RISING_EDGE, self.knob_rotate)
        self.pi.callback(self.sw, pigpio.EITHER_EDGE, self.button_click)
        self.pi.set_glitch_filter(self.clk, 1500)
        self.pi.set_glitch_filter(self.dt, 1500)
        self.pi.set_glitch_filter(self.sw, 5000)
        
    #Callback to trace dt states in real time
    def dt_changed(self, gpio, level, tick):
        self.last_dt_state = level

    # Check if button is pressed for 3 seconds
    def check_long_press(self):
        if self.button_pressed and not self.long_press_triggered:
            current_tick = self.pi.get_current_tick()
            press_duration = pigpio.tickDiff(self.button_press_start, current_tick)

            if press_duration >= 2000000:
                self.long_press_triggered = True
                self.long_press()

    # When knob is turned clockwise, direction is 1
    # Counter clockwise direction is -1
    # When knob is turned, executes method to handle rotation
    def knob_rotate(self, gpio, level, tick):
        elapsed = pigpio.tickDiff(self.last_tick, tick)
        if elapsed < 5000:
            return

        if self.last_dt_state == 0:
            direction = 1
        else:
            direction = -1
 
        fast_spin = elapsed < 100000
        self.last_tick = tick
        self.rotate(direction, fast_spin)

    # When button is clicked executes method to handle button click created in main
    # If button is pressed for 3 seconds or more return to main menu
    def button_click(self, gpio, level, tick):
        if level == 0:
            self.button_press_start = tick
            self.button_pressed = True
            self.long_press_triggered = False # Reset hold button flag
        elif level == 1:
            if self.button_pressed and not self.long_press_triggered:
                self.click()
            self.button_pressed = False
