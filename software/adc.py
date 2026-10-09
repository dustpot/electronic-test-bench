import pigpio
import time
class ADC:
    def __init__(self, pi, dac_pins, comp_pin):
        self.pi = pi
        self.dac_pins = dac_pins
        self.comp_pin = comp_pin
        self.pi.set_mode(self.comp_pin, pigpio.INPUT)
        self.pi.set_pull_up_down(self.comp_pin, pigpio.PUD_UP)
        for pin in self.dac_pins:
            self.pi.set_mode(pin, pigpio.OUTPUT)
            self.pi.write(pin, 0)

    def set_voltage(self, value):
        for i, pin in enumerate(self.dac_pins):
            bit = (value >> (5 - i)) & 1
            self.pi.write(pin, bit)

    # Reads voltage up to 3.3V
    # When high is detected on comparator, it will increase voltage
    # When low is detected on comparator, it will decrease voltage
    # Majority vote system: measure comparator 5 times, pass the majority outcome
    def read_voltage(self):
        current_val = 32
        for i in range(6):
            self.set_voltage(current_val)
            time.sleep(0.05)
            votes = 0
            for l in range(5): # Space each vote out chronologically to get varied values
                time.sleep(.01)
                votes += self.pi.read(self.comp_pin) 
            comp = 1 if votes >= 3 else 0
            change = 32 // (2 ** (i + 1))
            if comp:
                current_val += change
            else:
                current_val -= change
        return ((current_val / 63) * 3.0) + 0.05*((current_val / 63) * 3.0)

    def adc_cleanup(self):
        for pin in self.dac_pins:
            self.pi.write(pin, 0)
