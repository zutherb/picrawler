"""Gait definitions for the PiCrawler.

Pure data/geometry: every gait is a list of frames, each frame holds four
[x, y, z] leg-tip coordinates in mm (leg order: right front, left front,
left rear, right rear). No hardware access happens here.
"""
import math


class MoveList(dict):
    
    LENGTH_SIDE = 77
    X_DEFAULT = 45
    X_TURN = 70
    X_START = 0
    Y_DEFAULT = 45
    Y_TURN = 130
    Y_WAVE =120
    Y_START = 0 
    Z_DEFAULT = -50
    Z_UP = -30
    Z_WAVE = 60
    Z_TURN = -40
    Z_PUSH = -76
     
    # temp length
    TEMP_A = math.sqrt(pow(2 * X_DEFAULT + LENGTH_SIDE, 2) + pow(Y_DEFAULT, 2))
    TEMP_B = 2 * (Y_START + Y_DEFAULT) + LENGTH_SIDE
    TEMP_C = math.sqrt(pow(2 * X_DEFAULT + LENGTH_SIDE, 2) + pow(2 * Y_START + Y_DEFAULT + LENGTH_SIDE, 2))
    TEMP_ALPHA = math.acos((pow(TEMP_A, 2) + pow(TEMP_B, 2) - pow(TEMP_C, 2)) / 2 / TEMP_A / TEMP_B)
    # site for turn
    TURN_X1 = (TEMP_A - LENGTH_SIDE) / 2
    TURN_Y1 = Y_START + Y_DEFAULT / 2
    TURN_X0 = TURN_X1 - TEMP_B * math.cos(TEMP_ALPHA)
    TURN_Y0 = TEMP_B * math.sin(TEMP_ALPHA) - TURN_Y1 - LENGTH_SIDE

    def __init__(self, *args, **kwargs):
        dict.__init__(self, *args, **kwargs)
        self.z_current = self.Z_UP
        self.stand_position = 0
        self.recovery_step = []
        self.ready_state = 0
        self.angle = 30
   
    def __getitem__(self, item):
        # Look the gait up by attribute name instead of eval(), so an action
        # name coming from user/LLM input cannot execute arbitrary code.
        name = item.replace(" ", "_")
        if name.startswith("_"):
            raise AttributeError(item)
        return getattr(self, name)
    
    def turn_angle_coord(self, angle):
        a = math.atan(self.Y_DEFAULT/(self.X_DEFAULT+self.LENGTH_SIDE/2))
        angle1 = a/math.pi*180
        r1 = math.sqrt(pow(self.Y_DEFAULT,2)+ pow(self.X_DEFAULT+ self.LENGTH_SIDE/2, 2))
        x1 = r1* math.cos((angle1-angle)* math.pi/180)- self.LENGTH_SIDE/2
        y1 = r1* math.sin((angle1-angle)* math.pi/180)
        # print(x1,y1)
        
        x2 = (self.X_DEFAULT+ self.LENGTH_SIDE/2)* math.cos(angle*math.pi/180)- self.LENGTH_SIDE/2
        y2 = (self.X_DEFAULT+ self.LENGTH_SIDE/2)* math.sin(angle*math.pi/180)
        # print(x2,y2)
        
        b = math.atan((self.X_DEFAULT+self.LENGTH_SIDE/2)/(self.Y_DEFAULT+ self.LENGTH_SIDE))
        angle2 = b/math.pi*180
        r2 = math.sqrt(pow(self.X_DEFAULT+ self.LENGTH_SIDE/2, 2)+ pow(self.Y_DEFAULT+ self.LENGTH_SIDE,2))
        x3 = r2*math.sin((angle2-angle)* math.pi/180) - self.LENGTH_SIDE/2
        y3 = r2*math.cos((angle2-angle)*math.pi/180)- self.LENGTH_SIDE

        x3 += 10
        # print(x3,y3)
        return [x1,y1,x2,y2,x3,y3]
    
    # 装饰器封装函数,判断是否站立
    def check_stand(func):
        def wrapper(self):
            _action = []
            if not self.is_stand():
                _action += self.stand
            _action += func(self)
            return _action
        return wrapper
    
    # 装饰器封装函数，装饰器简化步态的0，1两种状态转化，状态0为2，3脚y轴为0，状态1为1，4脚y轴为0 mode为2种转化方式，mode0为1，2交换3，4交换，mode1为1，3交换2，4交换
    def normal_action(mode):
        def wrapper1(func):
            def wrapper2(self):
                _action = []
                if self.stand_position == 0:
                    _action += func(self)
                else:
                    temp = func(self)
                    new_step = []
                    for step in temp:
                        if mode == 0:
                            new_step = [step[1], step[0], step[3], step[2]]
                        elif mode == 1:
                            new_step = [step[2], step[3], step[0], step[1]]
                        _action += [new_step]
                return _action
            return wrapper2
        return wrapper1
    
    @property
    @normal_action(0)
    def sit(self):
        self.z_current = self.Z_UP
        return [[
            [self.X_DEFAULT,self.Y_DEFAULT,self.z_current],
            [self.X_TURN,self.Y_START,self.z_current],
            [self.X_TURN,self.Y_START,self.z_current],
            [self.X_DEFAULT,self.Y_DEFAULT,self.z_current],
        ]]
        

    @property
    @normal_action(0)
    def stand(self):
        _stand = []
        if self.ready_state ==  0:
            _stand += self.ready
        self.z_current = self.Z_DEFAULT
        # _stand += [[
        #     [self.X_DEFAULT,self.Y_DEFAULT,self.z_current],
        #     [self.X_DEFAULT,self.Y_START,self.z_current],
        #     [self.X_DEFAULT,self.Y_START,self.z_current],
        #     [self.X_DEFAULT,self.Y_DEFAULT,self.z_current],
        # ]]
        # return _stand
       
        # _stand += [
        #     # [[self.X_DEFAULT,self.Y_DEFAULT,self.z_current/3],[self.X_DEFAULT,self.Y_START,self.z_current/3],[self.X_DEFAULT,self.Y_START,self.z_current/3],[self.X_DEFAULT,self.Y_DEFAULT,self.z_current/3]],
        #     # [[self.X_DEFAULT,self.Y_DEFAULT,self.z_current*2/3],[self.X_DEFAULT,self.Y_START,self.z_current*2/3],[self.X_DEFAULT,self.Y_START,self.z_current*2/3],[self.X_DEFAULT,self.Y_DEFAULT,self.z_current*2/3]],
        #     [[self.X_DEFAULT,self.Y_DEFAULT,self.z_current/2],[self.X_DEFAULT,self.Y_START,self.z_current/2],[self.X_DEFAULT,self.Y_START,self.z_current*2],[self.X_DEFAULT,self.Y_DEFAULT,self.z_current*2/3]],
        #     [[self.X_DEFAULT,self.Y_DEFAULT,self.z_current],[self.X_DEFAULT,self.Y_START,self.z_current],[self.X_DEFAULT,self.Y_START,self.z_current],[self.X_DEFAULT,self.Y_DEFAULT,self.z_current]],
        # ]
        # return _stand
    

        _stand += [
            [[self.X_DEFAULT,self.Y_DEFAULT,self.z_current*0.35],
            [self.X_DEFAULT,self.Y_START,  self.z_current*0.35],
            [self.X_DEFAULT,self.Y_START,  self.z_current*0.35],
            [self.X_DEFAULT,self.Y_DEFAULT,self.z_current*0.35]],

            [[self.X_DEFAULT,self.Y_DEFAULT,self.z_current*0.55],
            [self.X_DEFAULT,self.Y_START,  self.z_current*0.55],
            [self.X_DEFAULT,self.Y_START,  self.z_current*0.55],
            [self.X_DEFAULT,self.Y_DEFAULT,self.z_current*0.55]],

            [[self.X_DEFAULT,self.Y_DEFAULT,self.z_current*0.75],
            [self.X_DEFAULT,self.Y_START,  self.z_current*0.75],
            [self.X_DEFAULT,self.Y_START,  self.z_current*0.75],
            [self.X_DEFAULT,self.Y_DEFAULT,self.z_current*0.75]],

            [[self.X_DEFAULT,self.Y_DEFAULT,self.z_current*0.9],
            [self.X_DEFAULT,self.Y_START,  self.z_current*0.9],
            [self.X_DEFAULT,self.Y_START,  self.z_current*0.9],
            [self.X_DEFAULT,self.Y_DEFAULT,self.z_current*0.9]],

            [[self.X_DEFAULT,self.Y_DEFAULT,self.z_current],
            [self.X_DEFAULT,self.Y_START,  self.z_current],
            [self.X_DEFAULT,self.Y_START,  self.z_current],
            [self.X_DEFAULT,self.Y_DEFAULT,self.z_current]],
        ]
        return _stand
    
    @property
    def ready(self):
        _ready = [[
            [self.X_DEFAULT,self.Y_DEFAULT,self.z_current],
            [self.X_TURN,self.Y_START,self.z_current],
            [self.X_TURN,self.Y_START,self.z_current],
            [self.X_DEFAULT,self.Y_DEFAULT,self.z_current],
        ]]
        self.ready_state = 1
        return _ready
      

    def is_sit(self):
        return self.z_current == self.Z_UP
        
    def is_stand(self):
        tmp = self.z_current == self.Z_DEFAULT
        # print("is stand? %s"%tmp)
        return tmp
    
    @property
    @check_stand
    @normal_action(0)
    def forward(self):
        return [
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_TURN, self.Y_START,self.Z_UP],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT*2,self.Z_UP],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT*2,self.z_current],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT,self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT*2, self.z_current]],
            
            [[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT,self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT*2, self.Z_UP]],
            [[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT,self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_TURN, self.Y_START, self.Z_UP]],
            [[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT,self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START, self.z_current]],
        ]
    
    @property
    @check_stand
    @normal_action(0)
    def backward(self):
        return [
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START,self.z_current],[self.X_TURN, self.Y_START, self.Z_UP],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START,self.z_current],[self.X_DEFAULT, self.Y_DEFAULT*2, self.Z_UP],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START,self.z_current],[self.X_DEFAULT, self.Y_DEFAULT*2, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.X_DEFAULT, self.Y_DEFAULT*2, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT,self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START, self.z_current]],
            [[self.X_DEFAULT, self.Y_DEFAULT*2, self.Z_UP],[self.X_DEFAULT, self.Y_DEFAULT,self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START, self.z_current]],
            [[self.X_TURN, self.Y_START, self.Z_UP],[self.X_DEFAULT, self.Y_DEFAULT,self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START, self.z_current]],
            [[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT,self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START, self.z_current]],
        ]
    
   
    @property
    @check_stand
    @normal_action(1)
    def turn_left(self):
        return [
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START,self.z_current],[self.X_TURN, self.Y_START, self.Z_UP],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.TURN_X1, self.TURN_Y1, self.z_current],[self.TURN_X1, self.TURN_Y1, self.z_current],[self.TURN_X0, self.TURN_Y0, self.Z_UP],[self.TURN_X0, self.TURN_Y0, self.z_current]],
            [[self.TURN_X1, self.TURN_Y1, self.z_current],[self.TURN_X1, self.TURN_Y1, self.z_current],[self.TURN_X0, self.TURN_Y0, self.z_current],[self.TURN_X0, self.TURN_Y0, self.z_current]],
            
            [[self.TURN_X1, self.TURN_Y1, self.z_current],[self.TURN_X1, self.TURN_Y1, self.z_current],[self.TURN_X0, self.TURN_Y0, self.z_current],[self.TURN_X0, self.TURN_Y0, self.Z_UP]],
            [[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_TURN, self.Y_START, self.Z_UP]],
            [[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START, self.z_current]],
        ]

    @property
    @check_stand
    @normal_action(1)
    def turn_right(self):
        return [
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_TURN, self.Y_START,self.Z_UP],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.TURN_X0, self.TURN_Y0, self.z_current],[self.TURN_X0, self.TURN_Y0, self.Z_UP],[self.TURN_X1, self.TURN_Y1, self.z_current],[self.TURN_X1, self.TURN_X1, self.z_current]],
            [[self.TURN_X0, self.TURN_Y0, self.z_current],[self.TURN_X0, self.TURN_Y0, self.z_current],[self.TURN_X1, self.TURN_Y1, self.z_current],[self.TURN_X1, self.TURN_X1, self.z_current]],
            [[self.TURN_X0, self.TURN_Y0, self.Z_UP],[self.TURN_X0, self.TURN_Y0, self.z_current],[self.TURN_X1, self.TURN_Y1, self.z_current],[self.TURN_X1, self.TURN_X1, self.z_current]],
            [[self.X_TURN, self.Y_START, self.Z_UP],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START, self.z_current]],
            [[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START, self.z_current]],
            
        ]
    
    @property
    def push_up(self):
        _push_up = []
        if not self.is_sit():
            _push_up += self.sit
        _push_up += [
            [[self.X_TURN, self.Y_START, self.Z_TURN],[self.X_TURN, self.Y_START, self.Z_TURN],[self.X_START, self.Y_TURN, self.Z_TURN],[self.X_START, self.Y_TURN,self.Z_TURN]],
            [[self.X_TURN, self.Y_START, self.Z_PUSH],[self.X_TURN, self.Y_START, self.Z_PUSH],[self.X_START, self.Y_TURN, self.Z_TURN],[self.X_START, self.Y_TURN,self.Z_TURN]],
            [[self.X_TURN, self.Y_START, self.Z_TURN],[self.X_TURN, self.Y_START, self.Z_TURN],[self.X_START, self.Y_TURN, self.Z_TURN],[self.X_START, self.Y_TURN,self.Z_TURN]],
            [[self.X_TURN, self.Y_START, self.Z_PUSH],[self.X_TURN, self.Y_START, self.Z_PUSH],[self.X_START, self.Y_TURN, self.Z_TURN],[self.X_START, self.Y_TURN,self.Z_TURN]],
            [[self.X_TURN, self.Y_START, self.Z_TURN],[self.X_TURN, self.Y_START, self.Z_TURN],[self.X_START, self.Y_TURN, self.Z_TURN],[self.X_START, self.Y_TURN,self.Z_TURN]],
            [[self.X_TURN, self.Y_START, self.Z_PUSH],[self.X_TURN, self.Y_START, self.Z_PUSH],[self.X_START, self.Y_TURN, self.Z_TURN],[self.X_START, self.Y_TURN,self.Z_TURN]],
            [[self.X_TURN, self.Y_START, self.Z_TURN],[self.X_TURN, self.Y_START, self.Z_TURN],[self.X_START, self.Y_TURN, self.Z_TURN],[self.X_START, self.Y_TURN,self.Z_TURN]],
        ]
        if self.stand_position == 0:
            _push_up.append([[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_TURN, self.Y_START,self.z_current],[self.X_TURN, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]])
        else:
            _push_up.append([[self.X_TURN, self.Y_START,self.z_current], [self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_TURN, self.Y_START, self.z_current]])
        return _push_up
    
    @property
    @check_stand
    @normal_action(0)
    def wave(self):
        return [
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_TURN, self.Y_START,self.Z_UP],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_START, self.Y_WAVE,self.Z_WAVE],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_START, self.Y_WAVE,self.Z_UP],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_START, self.Y_WAVE,self.Z_WAVE],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_START, self.Y_WAVE,self.Z_UP],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_START, self.Y_WAVE,self.Z_WAVE],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_START, self.Y_WAVE,self.Z_UP],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_TURN, self.Y_START,self.Z_UP],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START,self.z_current],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
        ]
    
    @property
    @check_stand
    @normal_action(1)
    def look_left(self):
        li = self.turn_angle_coord(self.angle)
        temp_x1 = li[0:2]
        temp_x1.append(self.z_current)
        temp_x2 = li[2:4]
        temp_x2.append(self.z_current)
        temp_x3 = li[4:6]
        temp_x3.append(self.z_current)
        return [
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START,self.z_current],[self.X_TURN, self.Y_START, self.Z_UP],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [temp_x1, temp_x2,[self.X_TURN, self.Y_START, self.Z_UP],temp_x3]
        ]
        
    @property
    @check_stand
    @normal_action(1)
    def look_right(self):
        li = self.turn_angle_coord(self.angle)
        temp_x1 = li[0:2]
        temp_x1.append(self.z_current)
        temp_x2 = li[2:4]
        temp_x2.append(self.z_current)
        temp_x3 = li[4:6]
        temp_x3.append(self.z_current)
        return [
            [
                [self.X_DEFAULT, self.Y_DEFAULT, self.z_current],
                [self.X_TURN, self.Y_START,self.Z_UP],
                [self.X_DEFAULT, self.Y_START, self.z_current],
                [self.X_DEFAULT, self.Y_DEFAULT, self.z_current]
            ],
            [temp_x3, [self.X_TURN, self.Y_START, self.Z_UP], temp_x2, temp_x1]
        ]
    
    @property
    @check_stand
    @normal_action(1)
    def turn_left_angle(self):
        li = self.turn_angle_coord(self.angle)
        temp_x1 = li[0]
        temp_y1 = li[1]
        temp_x2 = li[2]
        temp_y2 = li[3]
        temp_x3 = li[4]
        temp_y3 = li[5]
        return [
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START,self.z_current],[self.X_TURN, self.Y_START, self.Z_UP],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[temp_x1, temp_y1, self.z_current], [temp_x2, temp_y2, self.z_current],[self.X_TURN, self.Y_START, self.Z_UP],[temp_x3, temp_y3, self.z_current]],
            [[temp_x1, temp_y1, self.z_current], [temp_x2, temp_y2, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[temp_x3, temp_y3, self.z_current]],
            [[temp_x1, temp_y1, self.z_current], [temp_x2, temp_y2, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[temp_x3, temp_y3, self.Z_UP]],
            [[temp_x1, temp_y1, self.z_current], [temp_x2, temp_y2, self.z_current],[self.X_TURN, self.Y_DEFAULT, self.z_current],[self.X_TURN, self.Y_START, self.Z_UP]],
            [[temp_x1, temp_y1, self.z_current], [temp_x2, temp_y2, self.z_current],[self.X_TURN, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_START, self.z_current]]
        ]
        
    @property
    @check_stand
    @normal_action(1)
    def turn_right_angle(self):
        li = self.turn_angle_coord(self.angle)
        temp_x1 = li[0]
        temp_y1 = li[1]
        temp_x2 = li[2]
        temp_y2 = li[3]
        temp_x3 = li[4]
        temp_y3 = li[5]
        return [
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_TURN, self.Y_START,self.Z_UP],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
            [[temp_x3,temp_y3, self.z_current], [self.X_TURN, self.Y_START, self.Z_UP], [temp_x2, temp_y2, self.z_current], [temp_x1, temp_y1, self.z_current]],
            [[temp_x3,temp_y3, self.z_current], [self.X_DEFAULT, self.Y_DEFAULT, self.z_current], [temp_x2, temp_y2, self.z_current], [temp_x1, temp_y1, self.z_current]],
            [[temp_x3,temp_y3, self.Z_UP], [self.X_DEFAULT, self.Y_DEFAULT, self.z_current], [temp_x2, temp_y2, self.z_current], [temp_x1, temp_y1, self.z_current]],
            [[self.X_TURN, self.Y_START, self.Z_UP], [self.X_DEFAULT, self.Y_DEFAULT, self.z_current], [temp_x2, temp_y2, self.z_current], [temp_x1, temp_y1, self.z_current]],
            [[self.X_DEFAULT, self.Y_START, self.z_current], [self.X_DEFAULT, self.Y_DEFAULT, self.z_current], [temp_x2, temp_y2, self.z_current], [temp_x1, temp_y1, self.z_current]],
        ]
        
    
    @property
    @check_stand
    @normal_action(0)
    def look_up(self):
        return [
            [[self.X_DEFAULT, self.Y_DEFAULT, self.Z_DEFAULT],[self.X_DEFAULT, self.Y_START,self.Z_DEFAULT],[self.X_TURN, self.Y_START, self.Z_UP],[self.X_DEFAULT, self.Y_DEFAULT, self.Z_UP]],
        ]
        
    @property
    @check_stand
    @normal_action(0)
    def look_down(self):
        return [
            [[self.X_DEFAULT, self.Y_DEFAULT, self.Z_UP],[self.X_TURN, self.Y_START,self.Z_UP],[self.X_DEFAULT, self.Y_START, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
        ]
    
    def rotate_body_absolute_x(self, degree_x):
        degree_x = degree_x * math.pi / 180
        dz = (self.LENGTH_SIDE / 2 + self.Y_DEFAULT) * math.sin(degree_x)
        dy = (self.LENGTH_SIDE / 2 + self.Y_DEFAULT) * (1 - math.cos(degree_x))
        return [[self.X_DEFAULT, self.Y_DEFAULT - dy, self.Z_DEFAULT + dz],[self.X_DEFAULT, self.Y_DEFAULT - dy, self.Z_DEFAULT - dz],[self.X_DEFAULT, self.Y_DEFAULT - dy, self.Z_DEFAULT - dz],[self.X_DEFAULT, self.Y_DEFAULT - dy, self.Z_DEFAULT + dz]]
    
    
    def rotate_body_absolute_y(self, degree_y):
        degree_y = degree_y * math.pi / 180
        dz = (self.LENGTH_SIDE / 2 + self.X_DEFAULT) * math.sin(degree_y)
        dx = (self.LENGTH_SIDE / 2 + self.X_DEFAULT) * (1 - math.cos(degree_y))
        # print("dz = %d"%dz)
        # print("dx = %d"%dx)
        return [[self.X_DEFAULT- dx, self.Y_DEFAULT, self.Z_DEFAULT + dz], [self.X_DEFAULT- dx, self.Y_DEFAULT, self.Z_DEFAULT + dz],[self.X_DEFAULT- dx, self.Y_DEFAULT, self.Z_DEFAULT - dz],[self.X_DEFAULT- dx, self.Y_DEFAULT, self.Z_DEFAULT - dz]]
    
    
    def  move_body_absolute(self, x, y, z):
        return [[self.X_DEFAULT - x,self.Y_DEFAULT - y,self.Z_TURN - z],[self.X_DEFAULT + x,self.Y_DEFAULT - y,self.Z_TURN - z],[self.X_DEFAULT + x,self.Y_DEFAULT + y,self.Z_TURN - z],[self.X_DEFAULT - x,self.Y_DEFAULT + y,self.Z_TURN - z]]
    
    
    def to_rad(self, deg):
        return deg * math.pi / 180
    
    @property
    def dance(self):
        _dance = []
        if not self.is_sit():
            _dance += self.sit
        _dance += [
            [[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current],[self.X_DEFAULT, self.Y_DEFAULT, self.z_current]],
        ]
        for i in range(0, 360, 5):
            _dance.append(self.move_body_absolute(40 * math.sin(self.to_rad(i)), 40 * math.cos(self.to_rad(i)), 0))
        for i in range(360, 0, -5):
            _dance.append(self.move_body_absolute(40 * math.sin(self.to_rad(i)), 40 * math.cos(self.to_rad(i)), 0))
        _dance.append(self.rotate_body_absolute_x(-20))
        _dance.append(self.rotate_body_absolute_x(20))
        _dance.append(self.move_body_absolute(0, 0, 0))
        _dance.append(self.rotate_body_absolute_y(-20))
        _dance.append(self.rotate_body_absolute_y(20))
        for j in range(0, 3):
            for i in range(0, 360, 3):
                _dance.append(self.move_body_absolute(40 * math.sin(self.to_rad(i)), 40 * math.cos(self.to_rad(i)), (i / 360.0 + j) * 15))
        for j in range(3, 0, -1):
            for i in range(0, 360, 3):
                _dance.append(self.move_body_absolute(40 * math.sin(self.to_rad(i)), 40 * math.cos(self.to_rad(i)), ((360 - i) / 360.0 + j - 1) * 15))
        _dance.append(self.move_body_absolute(0, 0, 0))
        return _dance
