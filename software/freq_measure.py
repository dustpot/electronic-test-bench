import pigpio

class FreqMeasure:
  def __init__(self, pi, pin):
    # switching to method that measures time between each consecutrive edge
    self.pi = pi
    self.pin = pin
    self.pi.set_mode(pin, pigpio.INPUT)
    self.pi.set_glitch_filter(self.pin, 5)
   # self.last_tick = None # timestamp of last edge
    self.ticks = [] # list of timestamps for each rising edge
    self.max_readings = 100 # restrict list to X entries
    
    #self.final_tick = None
    #self.edge_count = 0

    self.cb = self.pi.callback(self.pin, pigpio.RISING_EDGE, self.cbf)

  def cbf(self, gpio, level, tick):
    self.ticks.append(tick) # record current rising edge timestamp
    if len(self.ticks) > self.max_readings: # drop oldest list entry when max_readings is exceeded
      self.ticks.pop(0)
    
  #  if self.edge_count == 0:
   #   self.initial_tick = tick

    #self.final_tick = tick
    #self.edge_count += 1

  # return median reading from list of frequency readings
  def read_freq(self):
    if len(self.ticks) < 3: 
      return 0.0
    total_time = pigpio.tickDiff(self.ticks[0], self.ticks[-1]) # total time from first stored edge to last
    if total_time > 0:
      # number of periods = number of edges - 1
      return (len(self.ticks) - 1) * 1000000.0 / total_time # freq = number of periods/total time
    return 0.0
   # self.cb.cancel()
    #count = self.edge_count
    #initial = self.initial_tick
    #final = self.final_tick

   # self.edge_count = 0
   # self.initial_tick = None
   # self.final_tick = None
    
    #self.cb = self.pi.callback(self.pin, pigpio.RISING_EDGE, self.cbf)

    if count > 1 and initial is not None and final is not None:
      time_diff_us = pigpio.tickDiff(initial, final)
      if time_diff_us > 0:
        return (count - 1) * 1000000.0 / time_diff_us

    return 0.0


  
