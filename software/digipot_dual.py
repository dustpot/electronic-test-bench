import pigpio
import time

class Digipot:
    # Initialize constructor variables
    def __init__(self, pi, channel):
        self.pi = pi
        self.speed = 50000
        
        self.handle = self.pi.spi_open(channel, self.speed, 0)
        
        # Transfer function values (wiper 0 = pot 1, wiper 1 = pot 2)
        self.slope1 = 72.7
        self.intercept1 = 122
        self.slope2 = 72.7
        self.intercept2 = 130

    # Changes resistance based on step number
    # Step is a number between 0 and 128
    def set_mcp4231_step(self, wiper, step):
        if wiper == 0:
            command = 0x00
        elif wiper == 1:
            command = 0x10
        else:
            print("Invalid wiper number use 0 or 1")
            return
        
        if step > 128:
            step = 128
        elif step < 0:
            step = 0
            
        self.pi.spi_write(self.handle, [command, step])
        print(f"MCP4231 set to step {step} on wiper{wiper}")

    def set_mcp4131_step(self, step):
        command = 0x00
        
        if step > 128:
            step = 128
        elif step < 0:
            step = 0
            
        self.pi.spi_write(self.handle, [command, step])
        print(f"MCP4231 set to step {step}")

    # Sets step of both wipers to 0 and turn off digipot
    def digipot_cleanup(self):
        self.set_mcp4231_step(0, 0)
        self.set_mcp4231_step(1, 0)
        self.pi.spi_close(self.handle)
