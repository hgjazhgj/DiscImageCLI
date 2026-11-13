// SPDX-License-Identifier: GPL-3.0-only
// CPU port of DiscColorStudio v0.8.5 LadderMath and LadderEncoder.
// Copyright (c) 2026 Disc Color Studio contributors.
// Include the shared strict-float geometry sampler; build as a separate DLL.
#include "dvd_kernel.c"

typedef struct { double x, y, z; } Vec;
typedef struct {
    int64_t responses, slots;
    double channel_bit_nm, step, exposure, half_log;
    Vec white;
    const double *periods, *gains;
    const int64_t *counts, *cycle, *rank;
    const uint8_t *bytes, *trim;
    const Vec *response, *off, *scenes;
} LadderModel;

static Vec vec(double x, double y, double z) { Vec v = {x,y,z}; return v; }
static Vec add(Vec a, Vec b) { return vec(a.x+b.x,a.y+b.y,a.z+b.z); }
static Vec sub(Vec a, Vec b) { return vec(a.x-b.x,a.y-b.y,a.z-b.z); }
static Vec mul(Vec a, double s) { return vec(a.x*s,a.y*s,a.z*s); }
static double dot(Vec a, Vec b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
static Vec cross(Vec a, Vec b) { return vec(a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x); }
static Vec unit(Vec a) { return mul(a,1.0/sqrt(dot(a,a))); }
static Vec lookup(const LadderModel *m, const Vec *values, double u)
{
    double f=clip(fabs(u)/m->step,0.0,(double)m->responses-1.0);
    int a=(int)f,b=a+1<m->responses?a+1:a;
    return add(values[a],mul(sub(values[b],values[a]),f-a));
}
static Vec weights(const LadderModel *m, int k, double u)
{
    double lam=log(fmax(u,1e-9)*m->periods[k]*m->channel_bit_nm);
    double red=clip((lam+m->half_log-log(580.0))/(2*m->half_log),0,1);
    double blue=clip((log(490.0)-lam+m->half_log)/(2*m->half_log),0,1);
    return vec(red,1-red-blue,blue);
}
static void affine(const LadderModel *m, double u, double view, Vec a[3], Vec *b)
{
    *b=lookup(m,m->off,view);
    a[0]=a[1]=a[2]=vec(0,0,0);
    for(int k=0;k<30;k++) {
        Vec w=weights(m,k,u);
        Vec difference=mul(sub(lookup(m,m->response+k*m->responses,view),*b),
            m->counts[k]/(double)m->slots*m->gains[k]);
        a[0]=add(a[0],mul(difference,w.x));
        a[1]=add(a[1],mul(difference,w.y));
        a[2]=add(a[2],mul(difference,w.z));
    }
    *b=mul(*b,1/m->exposure);
    for(int k=0;k<3;k++) a[k]=mul(a[k],1/m->exposure);
}
static double linear(double v) { return v<=.04045?v/12.92:pow((v+.055)/1.055,2.4); }
static double derivative(double x) {
    return x>pow(6.0/29,3)?1/(3*pow(fmax(x,1e-12),2.0/3)):1/(3*pow(6.0/29,2));
}
static Vec perceptual(Vec v, Vec d) {
    return vec(116*.65*v.y*d.y,500*(v.x*d.x-v.y*d.y),200*(v.y*d.y-v.z*d.z));
}
static void accumulate(Vec a[3],Vec target,Vec d,double weight,double h[9],double rhs[3])
{
    Vec q[3];
    for(int k=0;k<3;k++) q[k]=perceptual(a[k],d);
    target=perceptual(target,d);
    for(int k=0;k<3;k++) {
        rhs[k]+=weight*dot(q[k],target);
        for(int j=0;j<3;j++) h[k*3+j]+=weight*dot(q[k],q[j]);
    }
}
static void fit(const LadderModel *m,const uint8_t *color,double u,const double views[6],double stability,float out[3])
{
    Vec rgb=vec(linear(color[0]/255.0),linear(color[1]/255.0),linear(color[2]/255.0));
    double maximum=fmax(rgb.x,fmax(rgb.y,rgb.z));
    out[0]=out[1]=out[2]=0;
    if(maximum<=.003) return;
    Vec a[3],b;
    affine(m,u,u,a,&b);
    Vec xyz=vec(.412395588967414*rgb.x+.357583430763715*rgb.y+.180492647381701*rgb.z,
        .212586230785596*rgb.x+.715170303703411*rgb.y+.0722004986433362*rgb.z,
        .0192972154917469*rgb.x+.119183864580849*rgb.y+.95049712513158*rgb.z);
    Vec target=vec(xyz.x/.9505*m->white.x*.9,xyz.y*.9,xyz.z/1.089*m->white.z*.9);
    double tone=b.y+fmax(.9-b.y,0)*pow(xyz.y,.85);
    double lift=fmin(8,tone/fmax(target.y,1e-12));
    Vec goal=mul(target,lift);
    Vec d=vec(derivative(fmax(0,goal.x/m->white.x))/m->white.x,
        derivative(fmax(0,goal.y/m->white.y))/m->white.y,
        derivative(fmax(0,goal.z/m->white.z))/m->white.z);
    double h[9]={0},rhs[3]={0},s[3];
    double det=dot(a[0],cross(a[1],a[2]));
    Vec difference=sub(goal,b);
    if(fabs(det)>1e-14) {
        s[0]=clip(dot(difference,cross(a[1],a[2]))/det,0,1);
        s[1]=clip(dot(a[0],cross(difference,a[2]))/det,0,1);
        s[2]=clip(dot(a[0],cross(a[1],difference))/det,0,1);
    } else s[0]=s[1]=s[2]=.5;
    accumulate(a,difference,d,1,h,rhs);
    for(int n=0;n<6;n++) {
        affine(m,u,views[n],a,&b);
        accumulate(a,sub(goal,b),d,.25*stability/6,h,rhs);
    }
    for(int iteration=0;iteration<60;iteration++) {
        double change=0;
        for(int k=0;k<3;k++) {
            double residual=rhs[k];
            for(int j=0;j<3;j++) residual-=h[k*3+j]*s[j];
            double next=clip(s[k]+residual/fmax(h[k*3+k],1e-15),0,1);
            change=fmax(change,fabs(next-s[k]));s[k]=next;
        }
        if(change<2e-8) break;
    }
    double toe=clip((maximum-.003)/(.035-.003),0,1);
    toe=toe*toe*(3-2*toe);
    for(int k=0;k<3;k++) out[k]=(float)(s[k]*toe);
}
static double optical_u(double x,double y,Vec eye,Vec lamp)
{
    double r=sqrt(x*x+y*y);
    if(r<1e-9) return 0;
    Vec p=vec(x,y,0),g=add(unit(sub(eye,p)),unit(sub(lamp,p)));
    return fabs((g.x*y-g.y*x)/r);
}

EXPORT void ladder_fit_point(const LadderModel *m,const uint8_t *rgb,double u,
    const double *views,double stability,float *output) { fit(m,rgb,u,views,stability,output); }
EXPORT void ladder_fit_rows(const LadderModel *m,const uint8_t *rgb,int64_t width,int64_t height,
    double radius,double inner_radius,int64_t grid,int64_t row_start,int64_t row_count,
    double stability,float *field)
{
    double scale=(fmin((double)width,(double)height)-1)/(2*radius);
    for(int64_t row=row_start;row<row_start+row_count;row++) {
        double y=(row+.5)*2*radius/grid-radius;
        for(int64_t col=0;col<grid;col++) {
            float *out=field+(row*grid+col)*4;
            double x=(col+.5)*2*radius/grid-radius,r2=x*x+y*y;
            out[0]=out[1]=out[2]=0;out[3]=-1;
            if(r2<inner_radius*inner_radius||r2>radius*radius) continue;
            double u=optical_u(x,-y,m->scenes[0],m->scenes[1]),views[6];
            for(int k=0;k<6;k++) views[k]=optical_u(x,-y,m->scenes[2+2*k],m->scenes[3+2*k]);
            int64_t px=(int64_t)floor(clip((width-1)/2.0+x*scale,0,(double)width-1));
            int64_t py=(int64_t)floor(clip((height-1)/2.0+y*scale,0,(double)height-1));
            fit(m,rgb+(py*width+px)*3,u,views,stability,out);
            out[3]=(float)u;
        }
    }
}
static int frame_start(int p) { int a=(12+p)%172;return a==0||a==91; }
EXPORT void ladder_resolve_sector(const LadderModel *m,const uint8_t *code,uint8_t *output)
{
    int previous_start=0;uint8_t previous=0xA5;
    for(int start=0;start<2048;) {
        uint8_t c=code[start];int end=start+1;
        while(end<2048&&!frame_start(end)&&code[end]==c) end++;
        int carrier=c<30,prev_fill=start>0&&previous==0xA5;
        int fs=frame_start(start),ss=start==0,touching=carrier||(start>0&&previous<30);
        int behind=frame_start(previous_start)?0:(int)clip(2-(start-previous_start),0,2);
        int need=fs?0:ss?2:prev_fill?behind:touching?2:0;
        if(!(carrier||(c!=0xA5&&touching&&!prev_fill&&!fs&&!ss))) need=0;
        if(c==0x66&&ss) need=2;
        if(c==0x66&&prev_fill) need=behind;
        int length=end-start-need;
        int trim=carrier&&length>0&&end<2048&&!frame_start(end)?
            m->trim[c*92+(int)clip(length,0,91)]:0;
        for(int i=start;i<end;i++) output[i]=carrier?
            (i-start>=need&&i-start<need+length-trim?m->bytes[c*91+i-start-need]:0xA5):
            (i-start<need?0xA5:c);
        previous_start=start;previous=c;start=end;
    }
}
static uint8_t ladder_code(const LadderModel *m,const float *pixel,double angle)
{
    if(pixel[3]<0) return 0xA5;
    const double pi=3.1415926535897932384626433832795;
    int64_t turn=(int64_t)floor(angle/(2*pi)+.5);
    int slot=(int)((turn%m->slots+m->slots)%m->slots),v=(int)m->cycle[slot];
    int64_t cycle=(int64_t)floor(turn/(double)m->slots);
    int64_t rank=cycle*m->counts[v]+m->rank[slot];
    Vec w=weights(m,v,pixel[3]);
    double level=dot(w,vec(pixel[0],pixel[1],pixel[2]))*m->gains[v];
    double threshold=.5+v*.7548776662466927+rank*.5698402909980532;
    threshold-=floor(threshold);
    return level>threshold?(uint8_t)v:0x66;
}
EXPORT void ladder_generate(const Parameters *p,const LadderModel *m,const float *field,
    int64_t grid,double radius,const int64_t *centres,const uint8_t *masks,
    int64_t start_block,int64_t block_count,uint8_t *output)
{
    int64_t segment_index=0;
    uint8_t code[2048],resolved[2048];
    for(int64_t offset=0;offset<block_count;offset++) {
        int64_t block=start_block+offset,first_bit=block*INT64_C(619008);
        int64_t mask_index=(((block*16+p->psn_offset)>>4)&15)*2048;
        for(int sector=0;sector<16;sector++) {
            for(int byte=0;byte<2048;byte++) {
                int payload=sector*2048+byte;
                double arc=(double)(first_bit+centres[payload])*p->bit_length;
                double r,track_angle;
                position(p,arc,&segment_index,&r,&track_angle);
                double angle=p->start_angle+p->direction*track_angle;
                double x=r*cos(angle),y=-r*sin(angle);
                int64_t col=(int64_t)floor(clip((x+radius)*grid/(2*radius),0,(double)grid-1));
                int64_t row=(int64_t)floor(clip((y+radius)*grid/(2*radius),0,(double)grid-1));
                code[byte]=ladder_code(m,field+(row*grid+col)*4,track_angle);
            }
            ladder_resolve_sector(m,code,resolved);
            for(int byte=0;byte<2048;byte++) output[offset*32768+sector*2048+byte]=resolved[byte]^masks[mask_index+byte];
        }
    }
}
