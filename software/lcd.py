from RPLCD.i2c import CharLCD

# LCD Class
# Functionality for I2C display
class LCD:
    # Initialize LCD with I2C Address
    # Customize number of rows and columns
    def __init__(self):
        self.lcd = CharLCD(i2c_expander='PCF8574', address=0x27, port=1,
                           cols=20, rows=4, dotsize=8,
                           charmap='A02',
                           auto_linebreaks=True,
                           backlight_enabled=True)

    # Print text on LCD
    # Prints ">" on selected option
    def show_menu(self, item_name):
        self.lcd.clear()
        menu_items = ["Function Generator","Ohmmeter", "Voltmeter", "DC Reference"]
        function_menu = ["Frequency", "Amplitude", "Back"]

        if item_name in menu_items:
            active_menu = menu_items
        elif item_name in function_menu:
            active_menu = function_menu
            
        for i, option in enumerate(active_menu):
            self.lcd.cursor_pos = (i, 0)

            if option == item_name:
                self.lcd.write_string(f"> {option}")
            else:
                self.lcd.write_string(f" {option}")

    # Simple cleanup function for LCD
    def cleanup(self):
        self.lcd.clear()
        self.lcd.close()
