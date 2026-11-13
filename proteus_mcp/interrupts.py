"""C51 external-interrupt examples built using the general circuit API."""
from .devices import definition
from .library import default_library
from .model import CircuitError,atomic_write,json_bytes


SOURCE=r'''#include <REG51.H>
#define INITIAL_COUNT @INITIAL@
#define NESTING_MODE @NESTING@
sbit ISR0_ACTIVE = P2^4;
sbit ISR1_ACTIVE = P2^5;
sbit COUNT_EVENT = P3^5;
sbit WRAP_EVENT = P3^4;
sbit BUZZER_ON = P3^7;
volatile unsigned char ticks;
volatile unsigned int count = INITIAL_COUNT;
volatile unsigned char digits[4];
unsigned char code segments[10] = {0x3f,0x06,0x5b,0x4f,0x66,0x6d,0x7d,0x07,0x7f,0x6f};
unsigned char code selects[4] = {0x0e,0x0d,0x0b,0x07};
void timer0(void) interrupt 1 {
    static unsigned char scan;
    TH0=0xfc; TL0=0x18;
    ++ticks;
    P2=(P2&0xf0)|0x0f;
    P0=segments[digits[scan]];
    P2=(P2&0xf0)|selects[scan];
    scan=(scan+1)&3;
}
void wait_ms(unsigned char duration) {
    unsigned char start=ticks;
    while((unsigned char)(ticks-start)<duration) {}
}
void refresh_digits(void) {
    unsigned int n=count;
    digits[3]=n%10; n/=10;
    digits[2]=n%10; n/=10;
    digits[1]=n%10; n/=10;
    digits[0]=n;
}
void increment(void) {
    if(count==9999) {count=0; WRAP_EVENT=!WRAP_EVENT;}
    else ++count;
    refresh_digits();
    COUNT_EVENT=!COUNT_EVENT;
}
void ext0(void) interrupt 0 {
#if NESTING_MODE
    unsigned char i;
#endif
    ISR0_ACTIVE=0;
#if NESTING_MODE
    for(i=0;i<8;++i) {P1=~(1<<i); wait_ms(200);}
    P1=0xff;
#else
    increment(); BUZZER_ON=1; wait_ms(200); BUZZER_ON=0;
#endif
    ISR0_ACTIVE=1;
}
void ext1(void) interrupt 2 {
    ISR1_ACTIVE=0;
    increment(); BUZZER_ON=1;
#if NESTING_MODE
    /* Short bounded hold makes the nested interval directly observable. */
    {unsigned int n; for(n=0;n<10000;++n) {}}
#else
    /* Timer0 can preempt both low-priority external handlers in this mode. */
    wait_ms(200);
#endif
    BUZZER_ON=0;
    ISR1_ACTIVE=1;
}
void main(void) {
#if !NESTING_MODE
    unsigned char led=0;
    unsigned char start;
#endif
    P0=0; P1=0xff; P2=0xff; P3=0xff;
    BUZZER_ON=0; COUNT_EVENT=0; WRAP_EVENT=0;
    refresh_digits();
    TMOD=(TMOD&0xf0)|1; TH0=0xfc; TL0=0x18;
    ET0=1; PT0=1; TR0=1;
    IT0=0; IT1=0; EX0=1; EX1=1;
#if NESTING_MODE
    PX1=1;
#endif
    EA=1;
    while(1) {
#if NESTING_MODE
        P1=0xff;
#else
        P1=~(1<<led); start=ticks;
        while((unsigned char)(ticks-start)<200) {}
        led=(led+1)&7;
#endif
    }
}
'''


def external_interrupt(store,project,mode='level',initial_count=0):
    if mode not in ('level','nesting') or type(initial_count) is not int or not 0<=initial_count<=9999:
        raise CircuitError('External interrupt example: mode=level/nesting, initial_count=0..9999')
    library=str(default_library()/'ACTIVE.LIB')
    definition('BUZZER',library)
    store.create(project)
    ops=[]
    placements={}
    from .interrupt_layout import PLACEMENTS, ROTATIONS, plan
    def add(ref,device,position,props=None,library_path=None,rotation=0):
        position=PLACEMENTS[ref]
        rotation=ROTATIONS.get(ref,0)
        operation={'op':'add','ref':ref,'device':device,'position':[position[0]*10,-position[1]*10],
                   'properties':props or {},'rotation':rotation}
        if library_path:operation['library_path']=library_path
        ops.append(operation);placements[ref]=list(position)
    nets={}
    def net(name,*pins):nets.setdefault(name,[]).extend(pins)
    add('U1','AT89C51',[-12,5])
    add('SW1','BUTTON',[14,-20]);add('SW2','BUTTON',[27,-20])
    add('RP1','RES',[10,-14],{'value':'10k'},rotation=90)
    add('RP2','RES',[26,-14],{'value':'10k'},rotation=90)
    net('INT0','U1.12','SW1.1','RP1.1');net('INT1','U1.13','SW2.1','RP2.1')
    net('GND','U1.20','SW1.2','SW2.2');net('VCC','U1.40','U1.31','RP1.2','RP2.2')
    add('DSP1','7SEG-MPX4-CC',[15,30])
    for i,label in enumerate('ABCDEFGH'):
        add('RS'+str(i),'RES',[9+4*i,7],{'value':'330'},rotation=90)
        add('PU'+str(i),'RES',[9+4*i,-5],{'value':'10k'},rotation=270)
        net('SEG_'+label,'U1.'+str(39-i),'RS'+str(i)+'.1','PU'+str(i)+'.1')
        net(label+'_OUT','RS'+str(i)+'.2','DSP1.'+label)
        net('VCC','PU'+str(i)+'.2')
    for i in range(4):net('DIG_'+str(i+1),'U1.'+str(21+i),'DSP1.'+str(i+1))
    for i in range(8):
        add('D'+str(i+1),'LED-RED',[-32,7-i*4],rotation=90)
        add('RL'+str(i+1),'RES',[-42,7-i*4],{'value':'330'},rotation=180)
        net('LED'+str(i+1)+'_K','U1.'+str(i+1),'D'+str(i+1)+'.2')
        net('LED'+str(i+1)+'_A','D'+str(i+1)+'.1','RL'+str(i+1)+'.1');net('VCC','RL'+str(i+1)+'.2')
    add('BZ1','BUZZER',[44,-13],{'vnom':'5V','load':'1k','freq':'2000Hz'},library)
    add('Q1','BC547',[41,-25]);add('RB','RES',[31,-25],{'value':'4k7'})
    net('BUZZER_DRIVE','U1.17','RB.1');net('BUZZER_BASE','RB.2','Q1.2')
    net('BUZZER_LOW','Q1.1','BZ1.2');net('GND','Q1.3');net('VCC','BZ1.1')
    for i,(label,pin) in enumerate((('ISR0_ACTIVE','25'),('ISR1_ACTIVE','26'),('COUNT_EVENT','15'),('WRAP_EVENT','14'))):
        add('RD'+str(i),'RES',[8+9*i,-30],{'value':'47k'},rotation=270)
        net(label,'U1.'+pin,'RD'+str(i)+'.1');net('VCC','RD'+str(i)+'.2')
    add('RR','RES',[-4,23],{'value':'10k'},rotation=270)
    add('CR','CAP-ELEC',[-12,27],{'value':'10u'})
    add('X1','CRYSTAL',[-31,24],{'value':'12MHz'})
    add('CX1','CAP',[-40,18],{'value':'22p'},rotation=270)
    add('CX2','CAP',[-24,18],{'value':'22p'},rotation=270)
    add('CB','CAP',[4,27],{'value':'100n'},rotation=270)
    net('RESET','U1.9','RR.1','CR.2');net('GND','RR.2','CX1.2','CX2.2','CB.2')
    net('VCC','CR.1','CB.1');net('XTAL1','U1.19','X1.1','CX1.1');net('XTAL2','U1.18','X1.2','CX2.1')
    ops.extend({'op':'connect','name':n,'pins':p} for n,p in nets.items())
    store.edit(project,0,ops)
    source=SOURCE.replace('@INITIAL@',str(initial_count)).replace('@NESTING@','1' if mode=='nesting' else '0')
    atomic_write(store.folder(project)/'main.c',source.encode('ascii'))
    info={'mode':mode,'initial_count':initial_count,'placements':placements,'clock_hz':12000000,
          'routing':'schematic','sheet_size':[128,100],'wire_plan':plan(placements,ROTATIONS),
          'inputs':{'SW1':'INT0 low-level','SW2':'INT1 low-level'},'display':'4-digit common-cathode; P0 active-high segments, P2.0..3 active-low digit selects',
          'priority':'timer0 high, INT0 low, INT1 '+('high' if mode=='nesting' else 'low'),
          'diagnostics':'ISR0_ACTIVE/ISR1_ACTIVE are active-low; COUNT_EVENT toggles on increment; WRAP_EVENT toggles at 9999->0000',
          'limits':'simulation teaching circuit, not a certified physical multiplex current-driver design; buzzer drive != recorded audio'}
    atomic_write(store.folder(project)/'interrupt-example.json',json_bytes(info))
    return {'status':'created','project':project,**info,'revision':1,'native_export_required':True,'simulation_verified':False}
