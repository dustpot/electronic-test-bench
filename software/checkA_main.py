import pigpio
import time
import math
from adc import ADC
from digipot_dual import Digipot
from inputs import Inputs
from lcd import LCD
from freq_measure import FreqMeasure


class MainController:
    def __init__(self):
        self.pi = pigpio.pi()

        # --- Dual digipot (SPI channel 0) ---
        # Used by the ohmmeter for resistance measurement via binary search.
        # Pot 0 is the active search wiper, pot 1 is fixed at mid-scale as a reference divider.
        self.digipot_dual = Digipot(self.pi, channel=0)
        self.digipot_dual.set_mcp4231_step(0, 0)
        self.digipot_dual.set_mcp4231_step(1, 64)

        # --- Single digipot (SPI channel 1) ---
        # Controls the amplitude of the sine output by attenuating the filtered
        # GPIO13 signal before it enters the final TL084 gain stage.
        # Starts at 0 (output off) until the user explicitly enables output.
        self.digipot_single = Digipot(self.pi, channel=1)
        self.digipot_single.set_mcp4131_step(0)

        # --- GPIO output pin assignments ---
        # GPIO18 is used for the square wave via hardware PWM (clean, jitter-free).
        # GPIO13 is used for the sine wave via DMA pulse train fed into the RC filter chain.
        self.square_pin = 18
        self.sine_pin   = 13
        self.freq_pin = 24

        self.pi.set_mode(self.square_pin, pigpio.OUTPUT)
        self.pi.set_mode(self.sine_pin,   pigpio.OUTPUT)
        self.freq_measure = FreqMeasure(self.pi, self.freq_pin)

        # --- Function generator state defaults ---
        # waveform_type switches between the square and sine signal paths.
        # function_freq / function_amp are the confirmed active values.
        # target_freq / target_amp are the in-progress values while the user is scrolling.
        self.waveform_type = "Sine"

        self.function_freq = 1000
        self.target_freq   = 1000

        self.function_amp = 5.0
        self.target_amp   = 5.0

        # Function generator calibration table (data from fgen_test.py)
        self.amp_cal = [(0, 1.8), (2, 2.1), (4, 2.3), (8, 3.0), (16, 4.1), (24, 5.3), (32, 6.4),
                         (48, 8.8), (64, 11.0), (80, 13.4), (96, 15.6), (112, 18.0), (128, 20.9)]

        # Tracks the active DMA wave ID so we can stop and delete it cleanly.
        self.current_wave_id = None

        # --- DC reference defaults ---
        # dcref_step maps 0-16 to -5.0V to +5.0V in 0.625V increments.
        # dcref_value is the human-readable voltage, written to the 6-bit R2R DAC.
        self.dcref_value = 0.0
        self.dcref_step  = 8   # step 8 = 0.0V midpoint

        # --- LCD and menu setup ---
        self.display = LCD()

        # Each key is a named state; the list is the options shown on screen.
        # The rotary encoder scrolls through these and the button confirms selection.
        self.menu = {
            "main":         ["OFF", "Function Generator", "Ohmmeter", "Voltmeter", "DC Reference", "Freq Measurement"],
            "function":     ["Type", "Frequency", "Amplitude", "Output"],
            "type":         ["Square", "Sine", "Back", "Main"],
            "frequency":    ["Input Frequency", "Back", "Main"],
            "amplitude":    ["Input Amplitude", "Back", "Main"],
            "func_output":  ["On", "Off", "Back", "Main"],
            "ohmmeter":     ["Back", "Main"],
            "voltmeter":    ["Source"],
            "source":       ["External", "Internal Reference", "Back", "Main"],
            "dcref":        ["Voltage Value Input", "Output", "Back", "Main"],
            "dcref_output": ["On", "Off", "Back", "Main"],
            "freq_measure": ["Back", "Main"],
        }

        self.current_state  = "main"
        self.menu_index     = 0
        self.display_index  = 0

        # Rotary encoder on GPIO22/27, button on GPIO17.
        # Callbacks: rotate() handles scroll, click() handles press, long_press() returns to main.
        self.controls = Inputs(self.pi, 22, 27, 17, self.rotate, self.click, self.long_press)
        self.update_menu()

        # --- ADC / DAC setup ---
        # The 6-bit R2R DAC uses GPIO pins 12,25,16,19,20,21 as parallel binary output.
        # The ADC uses a comparator on GPIO23 for successive-approximation voltage reading.
        # GPIO5 is a separate comparator input used by the ohmmeter circuit.
        dac_pins = [12, 25, 16, 19, 20, 21]
        self.adc = ADC(self.pi, dac_pins, comp_pin=23)
        self.pi.set_mode(self.adc.comp_pin, pigpio.INPUT)
       # self.pi.set_pull_up_down(self.adc.comp_pin, pigpio.PUD_OFF)

        self.pi.set_mode(5, pigpio.INPUT)
        self.pi.set_pull_up_down(5, pigpio.PUD_OFF)


    # --- Rotary encoder scroll handler ---
    # Called by the Inputs class whenever the encoder is turned.
    # direction is +1 or -1; fast_spin is True if the user is spinning quickly.
    def rotate(self, direction, fast_spin):

        # Ohmmeter has a minimal two-item menu (Back / Main) rendered manually.
        if self.current_state == "ohmmeter" or self.current_state == "freq_measure":
            options = self.menu["ohmmeter"]
            self.menu_index = (self.menu_index + direction) % len(options)
            self.display.lcd.cursor_pos = (2, 0)
            self.display.lcd.write_string(f"{'> ' if self.menu_index == 0 else '  '}Back".ljust(20))
            self.display.lcd.cursor_pos = (3, 0)
            self.display.lcd.write_string(f"{'> ' if self.menu_index == 1 else '  '}Main".ljust(20))
            return

        # Standard menu navigation — wrap around at both ends.
        if self.current_state in self.menu:
            current_list = self.menu[self.current_state]
            max_index = len(current_list) - 1
            self.menu_index += direction

            if self.menu_index < 0:
                self.menu_index = max_index
            elif self.menu_index > max_index:
                self.menu_index = 0

            self.update_menu()

        # Frequency input: sine is constrained to 1–10 kHz in 500 Hz steps per spec.
        # Square wave allows finer control with fast-spin acceleration.
        elif self.current_state == "freq_input":
            if self.waveform_type == "Sine":
                self.target_freq = max(1000, min(10000, self.target_freq + (direction * 500)))
            else:
                change = 100 if fast_spin else 10
                self.target_freq = max(100, min(10000, self.target_freq + (direction * change)))

            self.display.lcd.cursor_pos = (2, 0)
            self.display.lcd.write_string(f"{self.target_freq}Hz".ljust(20))

        # Amplitude input: sine uses 0.625V steps per spec (16 steps across 0.625–10V).
        # Square wave allows finer resolution with fast-spin acceleration.
        elif self.current_state == "amp_input":
            if self.waveform_type == "Sine":
                self.target_amp = max(0.625, min(10.0, self.target_amp + (direction * 0.625)))
            else:
                self.target_amp = max(0.625, min(10.0, self.target_amp + (direction * 0.625))) # Could probably delete the if/else logic here, but not tryna rock the boat :)

            self.display.lcd.cursor_pos = (1, 0)
            self.display.lcd.write_string("Set Amplitude:".ljust(20))
            self.display.lcd.cursor_pos = (2, 0)
            self.display.lcd.write_string(f"{self.target_amp:.3f}Vpp".ljust(20))

        # DC reference input: 17 steps (0–16) mapping -5.0V to +5.0V in 0.625V increments.
        elif self.current_state == "dcref_input":
            self.dcref_step += direction
            if self.dcref_step < 0:
                self.dcref_step = 0
            elif self.dcref_step > 16:
                self.dcref_step = 16

            self.dcref_value = -5.0 + (self.dcref_step * 0.625)
            self.update_dcref_output()


    # --- Button click handler ---
    # Called on short press. Resolves the currently highlighted menu item
    # or confirms a value being entered in an input state.
    def click(self):

        if self.current_state in self.menu:
            selected_item = self.menu[self.current_state][self.menu_index]

            if selected_item == "OFF":
                # Full system shutdown: stop all waveforms, zero the DAC and digipot.
                self._turn_off_all_waveforms()
                self.digipot_single.set_mcp4131_step(0)
                self.write_dac(self.voltage_to_dac_code(0.0))
                self.display.lcd.clear()
                self.display.lcd.write_string("System OFF".ljust(20))
                time.sleep(1)
                self.change_menu_state("main")

            elif selected_item == "Function Generator":
                self.change_menu_state("function")

            elif selected_item == "Ohmmeter":
                self.current_state = "ohmmeter"
                self.menu_index = 0
                self._run_ohmmeter()

            elif selected_item == "Voltmeter":
                self.change_menu_state("voltmeter")

            
            elif selected_item == "Freq Measurement":
                self.current_state = "freq_measure"
                self.menu_index = 0
                #self.freq_measure.read_freq()
                #time.sleep(0.3)
               # print(f"edges: {self.freq_measure.edge_count}")
                freq = self.freq_measure.read_freq()
                self.display.lcd.clear()
                self.display.lcd.cursor_pos = (0, 0)
                self.display.lcd.write_string(f"Frequency:{freq:.1f}Hz".ljust(20))
                time.sleep(0.5)

                self.display.lcd.cursor_pos = (2, 0)
                self.display.lcd.write_string("> Back".ljust(20))
                self.display.lcd.cursor_pos = (3, 0)
                self.display.lcd.write_string(" Main".ljust(20))
            

            elif selected_item == "DC Reference":
                # Reset DC ref to 0V (step 8 = midpoint) each time we enter.
                self.dcref_step  = 8
                self.dcref_value = 0.0
                self.change_menu_state("dcref")

            elif selected_item == "Type":
                self.change_menu_state("type")

            elif selected_item == "Square":
                self.waveform_type = "Square"
                self.display.lcd.clear()
                self.display.lcd.write_string("Type: Square".ljust(20))
                time.sleep(1)
                self.change_menu_state("function")

            elif selected_item == "Sine":
                # Enforce minimum sine frequency in case the user previously had
                # a square wave set below 1 kHz.
                self.waveform_type = "Sine"
                self.target_freq = max(1000, self.target_freq)
                self.display.lcd.clear()
                self.display.lcd.write_string("Type: Sine".ljust(20))
                time.sleep(1)
                self.change_menu_state("function")

            elif selected_item == "Frequency":
                self.change_menu_state("frequency")

            elif selected_item == "Input Frequency":
                # Enter live frequency editing mode. target_freq mirrors function_freq
                # until the user confirms with a click.
                self.current_state = "freq_input"
                self.target_freq = self.function_freq
                self.display.lcd.clear()
                self.display.lcd.cursor_pos = (1, 0)
                self.display.lcd.write_string("Set Frequency".ljust(20))
                self.display.lcd.cursor_pos = (2, 0)
                self.display.lcd.write_string(f"{self.target_freq}Hz".ljust(20))

            elif selected_item == "Amplitude":
                self.change_menu_state("amplitude")

            elif selected_item == "Input Amplitude":
                # Enter live amplitude editing mode. target_amp mirrors function_amp
                # until the user confirms with a click.
                self.current_state = "amp_input"
                self.target_amp = self.function_amp
                self.display.lcd.clear()
                self.display.lcd.cursor_pos = (1, 0)
                self.display.lcd.write_string("Set Amplitude:".ljust(20))
                self.display.lcd.cursor_pos = (2, 0)
                self.display.lcd.write_string(f"{self.target_amp:.3f}Vpp".ljust(20))

            elif selected_item == "Output":
                if self.current_state == "function":
                    self.change_menu_state("func_output")
                elif self.current_state == "dcref":
                    self.change_menu_state("dcref_output")

            elif selected_item == "On":
                if self.current_state == "func_output":
                    # Always stop any running waveform before starting a new one
                    # to prevent DMA conflicts or PWM overlap.
                    self._turn_off_all_waveforms()

                    if self.waveform_type == "Square":
                        self.pi.hardware_PWM(self.square_pin, self.function_freq, 500000)
                        step = self.amp_to_step(self.function_amp + 1.375)
                        self.digipot_dual.set_mcp4231_step(1, step)

                    elif self.waveform_type == "Sine":
                        # Guard: sine path requires at least 1 kHz. If the stored
                        # frequency is lower (e.g. left over from a square wave session),
                        # clamp it before generating the DMA waveform.
                        safe_freq = max(1000, self.function_freq)
                        self._generate_sine_dma(safe_freq)

                        # Apply frequency-dependent amplitude correction before
                        # converting to a digipot step. This compensates for the
                        # RC filter attenuating higher frequencies — without this,
                        # a 5V setting at 10 kHz produces much less than 5V out.
                        corrected_amp = self._freq_amplitude_correction(self.function_amp/2, safe_freq)
                        step = self.sine_amp_to_step(corrected_amp)
                        self.digipot_single.set_mcp4131_step(step)

                    self.display.lcd.clear()
                    self.display.lcd.cursor_pos = (0, 0)
                    self.display.lcd.write_string(f"Out: ON ({self.waveform_type})".ljust(20))
                    self.display.lcd.cursor_pos = (1, 0)
                    self.display.lcd.write_string(f"Freq: {self.function_freq}Hz".ljust(20))
                    self.display.lcd.cursor_pos = (2, 0)
                    self.display.lcd.write_string(f"Amp: {self.function_amp:.3f}Vpp".ljust(20))

                elif self.current_state == "dcref_output":
                    self.display.lcd.clear()
                    self.update_dcref_output()
                    self.display.lcd.cursor_pos = (3, 0)
                    self.display.lcd.write_string(f"{self.dcref_value:+.2f}V".ljust(20))

            elif selected_item == "Off":
                if self.current_state == "func_output":
                    # Stop waveforms and mute the digipot so no signal leaks through.
                    self._turn_off_all_waveforms()
                    self.digipot_single.set_mcp4131_step(0)
                    self.display.lcd.cursor_pos = (3, 0)
                    self.display.lcd.write_string("Output: OFF".ljust(20))

                elif self.current_state == "dcref_output":
                    self.write_dac(self.voltage_to_dac_code(0.0))
                    self.display.lcd.cursor_pos = (3, 0)
                    self.display.lcd.write_string("Output: OFF".ljust(20))

            elif selected_item == "Source":
                self.change_menu_state("source")

            elif selected_item == "External":
                self.change_menu_state("voltmeter")
                self._read_external_voltage()

            elif selected_item == "Internal Reference":
                # Use the DC reference as the voltmeter's known source.
                self.dcref_step = 8
                self.dcref_value = 0.0
                self.current_state = "dcref_input"
                self.display.lcd.clear()
                self.display.lcd.cursor_pos = (0,0)
                self.display.lcd.write_string("DC Ref Input:".ljust(20))
                self.update_dcref_output()

            elif selected_item == "Voltage Value Input":
                self.current_state = "dcref_input"
                self.display.lcd.clear()
                self.display.lcd.cursor_pos = (0, 0)
                self.display.lcd.write_string("DC Ref Input:".ljust(20))
                self.update_dcref_output()

            elif selected_item == "Back":
                if self.current_state in ["ohmmeter", "voltmeter", "dcref", "freq_measure"]:
                    self.change_menu_state("main")
                elif self.current_state in ["type", "frequency", "amplitude"]:
                    self.change_menu_state("function")
                elif self.current_state == "func_output":
                    # Turning off output on back navigation keeps the hardware clean.
                    self._turn_off_all_waveforms()
                    self.digipot_single.set_mcp4131_step(0)
                    self.change_menu_state("function")
                elif self.current_state == "source":
                    self.change_menu_state("voltmeter")
                elif self.current_state == "dcref_output":
                    self.write_dac(self.voltage_to_dac_code(0.0))
                    self.change_menu_state("dcref")

            elif selected_item == "Main":
                # Always clean up active outputs when jumping straight to main.
                if self.current_state == "func_output":
                    self._turn_off_all_waveforms()
                    self.digipot_single.set_mcp4131_step(0)
                elif self.current_state == "dcref_output":
                    self.write_dac(self.voltage_to_dac_code(0.0))
                self.change_menu_state("main")

        # Frequency confirmed: commit target_freq to function_freq and apply immediately.
        elif self.current_state == "freq_input":
            self.function_freq = self.target_freq
            self._turn_off_all_waveforms()

            if self.waveform_type == "Square":
                self.pi.hardware_PWM(self.square_pin, self.function_freq, 500000)
                step = self.amp_to_step(self.function_amp + 1.375)
                self.digipot_dual.set_mcp4231_step(1, step)
            elif self.waveform_type == "Sine":
                self._generate_sine_dma(self.function_freq)
                # Reapply amplitude correction whenever frequency changes,
                # since each frequency has a different attenuation factor.
                corrected_amp = self._freq_amplitude_correction(self.function_amp/2, self.function_freq)
                step = self.sine_amp_to_step(corrected_amp)
                self.digipot_single.set_mcp4131_step(step)

            self.change_menu_state("frequency")

        # Amplitude confirmed: commit target_amp to function_amp and update digipot.
        elif self.current_state == "amp_input":
            self.function_amp = self.target_amp

            if self.waveform_type == "Sine":
                # Apply frequency correction here too so amplitude changes mid-session
                # are also corrected for the currently active frequency.
                self._generate_sine_dma(self.function_freq) # regenerate DMA after setting digipot
                corrected_amp = self._freq_amplitude_correction(self.function_amp/2, self.function_freq)
                step = self.sine_amp_to_step(corrected_amp)
                self.digipot_single.set_mcp4131_step(step)

            elif self.waveform_type == "Square":
                self.pi.hardware_PWM(self.square_pin, self.function_freq, 500000)
                step = self.amp_to_step(self.function_amp + 1.375)
                self.digipot_dual.set_mcp4231_step(1, step)

            self.change_menu_state("amplitude")

        # DC reference voltage confirmed: write the current value to the DAC and return.
        elif self.current_state == "dcref_input":
            self.update_dcref_output()
            self.change_menu_state("dcref")


    # --- Waveform generation and cleanup ---

    def _turn_off_all_waveforms(self):
        # Stops both the DMA sine wave and the hardware PWM square wave,
        # then drives both output pins low so nothing leaks through.
        self._stop_dma_wave()
        self.pi.hardware_PWM(self.square_pin, 0, 0)
        self.pi.write(self.square_pin, 0)
        self.pi.write(self.sine_pin, 0)

    def _generate_sine_dma(self, frequency):
        """
        Generates a PWM-shaped pulse train on GPIO13 using the pigpio DMA engine.

        Each period is divided into time slots. The duty cycle of each slot follows
        a positive-only sine curve (normalized 0 to 1). After passing through two
        cascaded RC low-pass filters (2.2 kΩ / 5 nF each, fc ≈ 14.5 kHz per stage),
        the stepped duty pattern is smoothed into an analog sine-like waveform.
        The MCP4131 digipot then attenuates this signal before the final TL084
        gain stage (Rf/Rg = 82k/33k, Av ≈ 3.48) sets the output amplitude.

        Sample count is stepped down at higher frequencies to keep each time slot
        wide enough for the pigpio DMA engine. Each slot needs at least 4 µs total
        (2 µs high + 2 µs low minimum). Going below this causes timing errors that
        corrupt the waveform shape and produce the wrong output frequency.

        Sample counts by frequency:
            1–2 kHz  : 32 samples  (slot ≈ 15–31 µs, very smooth)
            2–5 kHz  : 16 samples  (slot ≈ 12–25 µs, good)
            5–8 kHz  : 12 samples  (slot ≈ 10–16 µs, acceptable)
            8–10 kHz :  8 samples  (slot ≈ 12 µs, coarser shape but stable timing)
        """
        self._stop_dma_wave()

        if frequency <= 2000:
            samples = 32
        elif frequency <= 5000:
            samples = 16
        elif frequency <= 8000:
            samples = 12
        else:
            samples = 8

        period_us = int(round(1_000_000 / frequency))
        slot_us   = max(4, period_us // samples)   # hard floor of 4 µs per slot

        pulses = []

        for i in range(samples):
            theta = 2.0 * math.pi * i / samples
            full_scale_threshold = 3.0
            scale = min((self.function_amp / 2) / full_scale_threshold, 1.0)
            # Positive-only normalized sine: ranges from 0.0 to 1.0.
            # This keeps the filtered output in the positive voltage domain (0 to Vpeak).
            normalized = 0.5 * (1.0 + scale * math.sin(theta))

            # Clamp so we always have at least 1 µs high and 1 µs low per slot.
            high_us = int(round(normalized * slot_us))
            high_us = max(1, min(slot_us - 1, high_us))
            low_us  = slot_us - high_us

            pulses.append(pigpio.pulse(1 << self.sine_pin, 0, high_us))
            if low_us > 0:
                pulses.append(pigpio.pulse(0, 1 << self.sine_pin, low_us))

        self.pi.wave_clear()
        self.pi.wave_add_generic(pulses)
        self.current_wave_id = self.pi.wave_create()

        if self.current_wave_id >= 0:
            self.pi.wave_send_repeat(self.current_wave_id)

    def _stop_dma_wave(self):
        # Stop the DMA engine and clear all wave data.
        # wave_clear() removes everything so no separate wave_delete is needed.
        # Drive the sine pin low explicitly so no residual voltage sits on the output.
        self.pi.wave_tx_stop()
        self.pi.wave_clear()
        self.current_wave_id = None
        self.pi.write(self.sine_pin, 0)


    # --- Instrument measurement functions ---

    def _run_ohmmeter(self):
        """
        Measures resistance using a 7-step binary search on the dual digipot.

        The digipot wiper is driven to successive midpoints until it matches
        the unknown resistance, detected by the comparator on the ADC comp pin.
        The final step is converted to ohms using the calibrated slope and intercept
        stored in the Digipot object.
        """
        self.digipot_dual.set_mcp4231_step(1, 64)
        current_step = 64
        for i in range(7):
            self.digipot_dual.set_mcp4231_step(0, current_step)
            time.sleep(0.05)
            change = 64 // (2 ** (i + 1))

            if self.pi.read(5):
                current_step += change
            else:
                current_step -= change

        final_res   = current_step * self.digipot_dual.slope1 + self.digipot_dual.intercept1
        uncertainty = self.digipot_dual.slope1 / 2.0

        self.display.lcd.clear()
        self.display.lcd.cursor_pos = (0, 0)
        self.display.lcd.write_string(f"{int(final_res)} +/-{int(uncertainty)} Ohms".ljust(20))
        self.display.lcd.cursor_pos = (2, 0)
        self.display.lcd.write_string("> Back".ljust(20))
        self.display.lcd.cursor_pos = (3, 0)
        self.display.lcd.write_string("  Main".ljust(20))

    def _read_external_voltage(self):
        """
        Reads an external voltage via the ADC comparator path.

        The ADC returns a voltage in the 0–3.3V comparator range. The circuit
        centres the input around 1.65V (half of 3.3V), so we de-centre and
        scale back to the original ±5V input range.

        Resolution is derived from the 6-bit DAC step size (3.3V / 63 steps),
        scaled through the same gain factor. Uncertainty is reported as half a step.
        """
        adc_voltage      = self.adc.read_voltage()
        measured_voltage = ((adc_voltage - 1.65) / 1.65) * 5

        resolution      = 3.3 / 63
        real_resolution = (resolution / 1.65) * 5
        uncertainty     = real_resolution / 2

        self.display.lcd.clear()
        self.display.lcd.cursor_pos = (0, 0)
        self.display.lcd.write_string("Measured Voltage:".ljust(20))
        self.display.lcd.cursor_pos = (1, 0)
        self.display.lcd.write_string(f"{measured_voltage:.2f}V".ljust(20))
        self.display.lcd.cursor_pos = (2, 0)
        self.display.lcd.write_string(f"+/- {uncertainty:.2f}V".ljust(20))
        


    # --- Menu display helpers ---

    def return_to_menu(self):
        # Long-press escape: shut everything down and return to the main menu.
        self._turn_off_all_waveforms()
        self.digipot_single.set_mcp4131_step(0)
        self.write_dac(self.voltage_to_dac_code(0.0))

        self.display.lcd.clear()
        self.display.lcd.cursor_pos = (0, 0)
        self.display.lcd.write_string("Returning to menu...".ljust(20))
        time.sleep(1)
        self.change_menu_state("main")

    def update_menu(self):
        """
        Renders up to 4 menu items on the LCD, scrolling the visible window
        as the user navigates past the top or bottom of the display.
        The selected item is prefixed with "> ", others with "  ".
        """
        current_list = self.menu[self.current_state]
        max_items    = len(current_list)

        # Scroll the display window to keep the selected item always visible.
        if self.menu_index >= self.display_index + 4:
            self.display_index = self.menu_index - 3
        elif self.menu_index < self.display_index:
            self.display_index = self.menu_index

        for r in range(4):
            item = self.display_index + r
            self.display.lcd.cursor_pos = (r, 0)
            if item < max_items:
                prefix = "> " if item == self.menu_index else "  "
                text   = f"{prefix}{current_list[item]}"
            else:
                text = " " * 20
            self.display.lcd.write_string(text.ljust(20))

    def change_menu_state(self, new_state):
        # Switch to a new menu state and reset the cursor to the top.
        self.current_state  = new_state
        self.menu_index     = 0
        self.display_index  = 0
        self.update_menu()

    def long_press(self):
        # Long press from any state except main returns to the main menu.
        if self.current_state != "main":
            self.return_to_menu()

    def cleanup_and_exit(self):
        # Called on KeyboardInterrupt. Shuts down all hardware cleanly before exit.
        self._turn_off_all_waveforms()
        self.digipot_dual.digipot_cleanup()
        self.digipot_single.digipot_cleanup()
        self.adc.adc_cleanup()
        self.display.cleanup()
        self.pi.stop()
        exit()


    # --- DC reference DAC helpers ---

    def voltage_to_dac_code(self, vref):
        """
        Converts a target voltage (-5V to +5V) into a 6-bit DAC code (0–63).

        A linear calibration correction (slope CAL_M, offset CAL_B) is applied
        first to compensate for component tolerances in the R2R ladder circuit.
        The corrected voltage is then normalized to the 0–63 integer range.
        """
        MIN_V, MAX_V = -5.0, 5.0
        CAL_M, CAL_B = 1.17, 0.72
        corrected_vref = (vref - CAL_B) / CAL_M
        corrected_vref = max(MIN_V, min(MAX_V, corrected_vref))
        normalized     = (corrected_vref - MIN_V) / (MAX_V - MIN_V)
        dac_code       = round(normalized * 63)
       # print(f"voltage={self.dcref_value}, corrected={corrected_vref}, dac_code={dac_code}")
        return max(0, min(63, dac_code))

    def step_to_amp(self, step):
        cal = self.amp_cal
        # Clamp lower and upper bounds of allowed amplitudes (defined by calibration points)
        if step <= cal[0][0]:
            return cal[0][1]
        if step >= cal[-1][0]:
            return cal[-1][1]
        # Cycle through consecutive calibration point pairs
        for i in range(len(cal) - 1):
            s1, a1 = cal[i] # lower pair (step, amplitude)
            s2, a2 = cal[i + 1] # upper pair (step, amp)
            # Check if desired step falls between the two points
            if s1 <= step <= s2:
                fraction = (step - s1)/(s2 - s1) # Find how far between the two points as a fraction
                return a1 + fraction * (a2 - a1) # Find the amplitude that corresponds to that fraction
        return cal[-1][1]


    def amp_to_step(self, desired_amp):
        cal = self.amp_cal
        # Clamp step to upper and lower limits (defined by calibration points)
        if desired_amp <= cal[0][1]:
            return cal[0][0]
        if desired_amp >= cal[-1][1]:
            return cal[-1][0]

        # Cycle through pairs of calibration points
        for i in range(len(cal) - 1):
            s1, a1 = cal[i] # lower calibration point (step, amp)
            s2, a2 = cal[i + 1] # upper calibration point (step, amp)

            # Check if amplitude is between points
            if a1 <= desired_amp <= a2:
                fraction = (desired_amp - a1)/(a2 - a1) # Find how far between points as fraction
                return int(round(s1 + fraction * (s2 - s1))) # Find the corresponding step
        return cal[-1][0]


    
    def write_dac(self, code):
        # Write a 6-bit code to the R2R DAC by setting each GPIO pin to the
        # corresponding bit, MSB first (pin index 0 = bit 5).
        for i, pin in enumerate(self.adc.dac_pins):
            bit = (code >> (5 - i)) & 1
            self.pi.write(pin, bit)

    def update_dcref_output(self):
        # Compute the DAC code for the current dcref_value, write it to hardware,
        # and update the LCD to show the set voltage and raw DAC code.
        dac_code = self.voltage_to_dac_code(self.dcref_value)
        self.write_dac(dac_code)
        self.display.lcd.cursor_pos = (1, 0)
        self.display.lcd.write_string(f"Set: {self.dcref_value:+.3f}V".ljust(20))
        self.display.lcd.cursor_pos = (2, 0)
        self.display.lcd.write_string(f"DAC: {dac_code:02d}".ljust(20))
        


    # --- Sine amplitude calibration ---

    def _freq_amplitude_correction(self, desired_amp, frequency):
        """
        Scales the desired amplitude upward to compensate for RC filter attenuation
        at higher frequencies.

        The RC filter (2.2 kΩ / 5 nF, fc ≈ 14.5 kHz per stage) attenuates signals
        above its cutoff. Without correction, a 5V setting at 10 kHz produces much
        less than 5V at the output. This function scales the amplitude request up so
        the digipot pushes harder and the output lands closer to the target.

        Correction factors are derived from measured attenuation at each frequency
        relative to the 1 kHz reference (where the filter has minimal effect):
            1 kHz  : 5.3V measured → correction 1.00 (reference, no change)
            2 kHz  : 4.9V measured → correction 5.3/4.9 = 1.08
            5 kHz  : 4.0V measured → correction 5.3/4.0 = 1.33
            10 kHz : 2.7V measured → correction 5.3/2.7 = 1.96

        Frequencies between the measured points are linearly interpolated so
        correction changes smoothly rather than jumping at the boundaries.

        The corrected amplitude is clamped to 10V so we never ask the digipot
        for more than the circuit can deliver.

        If the RC filter components change, re-measure attenuation at each
        frequency and update the correction factors here.
        """

        # Correction lookup: (frequency_hz, correction_factor)
        # At 1 kHz the output is our calibration reference so correction is 1.0.
        # Higher frequencies need progressively more correction as the filter
        # rolls off more of the signal.
        correction_table = [
            (1000,  9.8/12.9),
            (2000,  9.8/11.9),
            (5000,  9.8/11.1),
            (10000, 1.00),
        ]

        # Find the two bracketing entries and linearly interpolate between them.
        for i in range(len(correction_table) - 1):
            f_low,  c_low  = correction_table[i]
            f_high, c_high = correction_table[i + 1]

            if f_low <= frequency <= f_high:
                # Linear interpolation between the two nearest measured points.
                t = (frequency - f_low) / (f_high - f_low)
                correction = c_low + t * (c_high - c_low)
                corrected = desired_amp * correction
                return min(corrected, 10.0)

        # If frequency is below the table minimum, no correction needed.
        if frequency <= correction_table[0][0]:
            return desired_amp

        # If frequency is above the table maximum, use the highest correction.
        return min(desired_amp * correction_table[-1][1], 10.0)

    def sine_amp_to_step(self, desired_amp):
        """
        Converts a target amplitude in volts to an MCP4131 wiper step (0–128).

        Calibrated with 2.2 kΩ RC resistors, 5 nF filter caps, and 82 kΩ
        feedback resistor at 1 kHz. Gain stage: Av = 1 + (82k/33k) ≈ 3.48.

        This function receives the already frequency-corrected amplitude from
        _freq_amplitude_correction, so it only needs to handle the 1 kHz
        step-to-voltage mapping. All frequency compensation is done upstream.

        Measured bench data at 1 kHz (10x probe):
            1.25V → 1.4V    2.5V → 2.7V    5.0V → 5.3V
            7.5V  → 7.8V    8.75V → 9.0V   10V  → 10.3V

        The slight overshoot at the top (10.3V at a 10V setting) is from the
        TL084 being pushed close to its rail — the high range slope accounts
        for this by being marginally shallower to avoid hard clipping.

        Low range  (0–5V):   step ≈ 12.0 × V − 6.5
        High range (5–10V):  step ≈ 11.2 × V − 3.0
        """
        """
        if desired_amp <= 5.0:
            # Low range: linear fit across the 1.25V to 5V measured points.
            step = round(12.0 * desired_amp - 6.5)
        else:
            # High range: slightly shallower slope to avoid pushing into
            # hard clipping at the top of the TL084 output swing.
            step = round(11.2 * desired_amp - 3.0)
            
        return max(0, min(128, step))
        """
        # ^Ditching this (for now)
        # Mapping 0-10V directly onto 0-128 steps
        step = round((desired_amp-.3125)/(5.0-.3125)*128)
        return max(0, min(128, step))

    def step_to_sine_amp(self, step):
        """
        Inverse estimate of sine_amp_to_step.
        Uses the low-range linear fit as an approximation. For display only.
        """
        amp = (step + 6.5) / 12.0
        return max(0.0, min(10.0, amp))


    # --- Main loop ---

    def run(self):
        # Spin forever checking for long-press events.
        # Short press and rotation are handled by interrupt callbacks in Inputs.
        try:
            while True:
                self.controls.check_long_press()
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.cleanup_and_exit()


if __name__ == "__main__":
    app = MainController()
    app.run()
