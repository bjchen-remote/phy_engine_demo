/* Massless Hooke/dashpot connections and hard distance constraints on point masses.
 * Springs: kick-drift-kick Verlet, exact pair dashpot symmetric splitting.
 * Rods: SHAKE positions along old gradients and RATTLE velocity projection.
 * Ropes: unilateral current-gradient projection; engagement is inelastic.
 * Finite constraint iteration and rope projection can dissipate energy. No collision,
 * self collision, bending, rotational inertia, or implicit stiffness is claimed. */
#define _POSIX_C_SOURCE 200809L
#include "connections_native.h"
#include <math.h>
#include <stddef.h>
#include <string.h>
#include <time.h>

#define MAX_NODES 64
#define MAX_CONNECTIONS 256
#define MAX_FIELDS 8
typedef struct { double x, y, z; } Vec;
typedef struct {
    ConnectionSimulation *s; ConnectionDiagnostics *d;
    double started, inverse_mass[MAX_NODES], acceleration[3*MAX_NODES];
    double previous[3*MAX_NODES], macro_start[3*MAX_NODES];
    Vec initial_direction[MAX_CONNECTIONS];
} Work;
static Vec v(double x,double y,double z) { Vec a={x,y,z};return a; }
static Vec get(const double *p,int i) { return v(p[3*i],p[3*i+1],p[3*i+2]); }
static void put(double *p,int i,Vec x) {p[3*i]=x.x;p[3*i+1]=x.y;p[3*i+2]=x.z;}
static Vec add(Vec a,Vec b) {return v(a.x+b.x,a.y+b.y,a.z+b.z);}
static Vec sub(Vec a,Vec b) {return v(a.x-b.x,a.y-b.y,a.z-b.z);}
static Vec mul(Vec a,double k) {return v(a.x*k,a.y*k,a.z*k);}
static double dot(Vec a,Vec b) {return a.x*b.x+a.y*b.y+a.z*b.z;}
static double length(Vec a) {return sqrt(dot(a,a));}
static Vec cross(Vec a,Vec b) {return v(a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x);}
static int finite_vec(Vec a) {return isfinite(a.x)&&isfinite(a.y)&&isfinite(a.z);}
static double now(void) {struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9;}
static int expired(Work *w) {return now()-w->started>=w->s->deadline_seconds;}
static double tolerance(double rest) {return fmax(1e-9,1e-7*rest);}

static double geometry(Work *w,int j,Vec *normal) {
    Connection *e=&w->s->connection[j];
    Vec delta=sub(get(w->s->positions,e->b),get(w->s->positions,e->a));
    double len=length(delta);
    *normal=len>1e-14?mul(delta,1/len):w->initial_direction[j];
    return len;
}
static Vec field_acceleration(Work *w,int i,double t) {
    ConnectionSimulation *s=w->s;Vec result=v(0,0,0),p=get(s->positions,i);
    for(int j=0;j<s->fields;j++) {
        ConnectionField *f=&s->field[j];
        if(!(s->field_mask[i]&(UINT32_C(1)<<j)) || t<f->start_time || t>=f->end_time) continue;
        if(f->type==0) {result=add(result,get(f->vector,0));continue;}
        Vec r=sub(p,get(f->origin,0)),axis=get(f->vector,0);
        if(f->type==2) r=sub(r,mul(axis,dot(r,axis)));
        double radius=length(r);if(radius<=1e-12 || radius>=f->radius) continue;
        Vec direction=mul(r,1/radius);double falloff=1-radius/f->radius;
        if(f->type==1) result=add(result,mul(direction,f->strength*falloff));
        else {
            Vec tangent=cross(axis,direction);double size=length(tangent);
            if(size>1e-12) result=add(result,mul(tangent,f->strength*falloff/size));
            result=sub(result,mul(direction,f->secondary_strength*falloff));
        }
    }
    return result;
}
static int accelerations(Work *w,double t) {
    ConnectionSimulation *s=w->s;
    for(int i=0;i<s->nodes;i++) put(w->acceleration,i,s->fixed[i]?v(0,0,0):field_acceleration(w,i,t));
    for(int j=0;j<s->connections;j++) {
        Connection *e=&s->connection[j];if(e->type!=0) continue;
        Vec n;double len=geometry(w,j,&n);
        /* The gradient of a positive-rest-length spring is undefined at coincidence. */
        if(!(len>1e-14)) return 0;
        Vec force=mul(n,e->stiffness*(len-e->rest));
        put(w->acceleration,e->a,add(get(w->acceleration,e->a),mul(force,w->inverse_mass[e->a])));
        put(w->acceleration,e->b,sub(get(w->acceleration,e->b),mul(force,w->inverse_mass[e->b])));
    }
    return 1;
}
static void damping(Work *w,double h,int reverse) {
    ConnectionSimulation *s=w->s;
    for(int j=0;j<s->connections;j++) {
        int index=reverse?s->connections-1-j:j;Connection *e=&s->connection[index];
        if(e->type!=0 || e->damping==0) continue;
        Vec n;geometry(w,index,&n);
        double wa=w->inverse_mass[e->a],wb=w->inverse_mass[e->b],sum=wa+wb;
        double u=dot(sub(get(s->velocities,e->b),get(s->velocities,e->a)),n);
        double impulse=-expm1(-e->damping*sum*h)*u/sum;
        put(s->velocities,e->a,add(get(s->velocities,e->a),mul(n,wa*impulse)));
        put(s->velocities,e->b,sub(get(s->velocities,e->b),mul(n,wb*impulse)));
    }
}
static int project_positions(Work *w) {
    ConnectionSimulation *s=w->s;
    for(int iteration=0;iteration<s->iterations;iteration++) {
        for(int j=0;j<s->connections;j++) {
            Connection *e=&s->connection[j];if(e->type==0) continue;
            Vec n;double error=geometry(w,j,&n)-e->rest;
            if(e->type==2 && error<=0) continue;
            double wa=w->inverse_mass[e->a],wb=w->inverse_mass[e->b];
            Vec shift;
            if(e->type==1) {
                /* SHAKE corrects along the previous constraint gradient. Solving
                   the local quadratic exactly avoids the O(h) pendulum damping
                   of ordinary current-direction position projection. */
                Vec old_delta=sub(get(w->previous,e->b),get(w->previous,e->a));
                double old_length=length(old_delta);if(!(old_length>1e-14)) return 0;
                Vec old_normal=mul(old_delta,1/old_length);
                Vec current=sub(get(s->positions,e->b),get(s->positions,e->a));
                double along=dot(current,old_normal),squared_error=error*(length(current)+e->rest);
                double discriminant=along*along-squared_error;
                if(!(along>0) || discriminant<0) return 0; /* timestep cannot resolve the rotation */
                double denominator=along+sqrt(discriminant);
                if(!(denominator>0)) return 0;
                shift=mul(old_normal,squared_error/((wa+wb)*denominator));
            } else shift=mul(n,error/(wa+wb));
            put(s->positions,e->a,add(get(s->positions,e->a),mul(shift,wa)));
            put(s->positions,e->b,sub(get(s->positions,e->b),mul(shift,wb)));
        }
    } return 1;
}
static void project_velocities(Work *w) {
    ConnectionSimulation *s=w->s;
    for(int iteration=0;iteration<s->iterations;iteration++) {
        for(int j=0;j<s->connections;j++) {
            Connection *e=&s->connection[j];if(e->type==0) continue;
            Vec n;double len=geometry(w,j,&n);
            double speed=dot(sub(get(s->velocities,e->b),get(s->velocities,e->a)),n);
            if(e->type==2 && (len<e->rest-fmax(1e-12,e->rest*1e-10) || speed<=0)) continue;
            double wa=w->inverse_mass[e->a],wb=w->inverse_mass[e->b];
            Vec impulse=mul(n,speed/(wa+wb));
            put(s->velocities,e->a,add(get(s->velocities,e->a),mul(impulse,wa)));
            put(s->velocities,e->b,sub(get(s->velocities,e->b),mul(impulse,wb)));
        }
    }
}
static int step(Work *w,double t,double h) {
    ConnectionSimulation *s=w->s;size_t bytes=3*(size_t)s->nodes*sizeof(double);
    damping(w,h*.5,0);
    if(!accelerations(w,t+.5*h)) return 0;
    memcpy(w->previous,s->positions,bytes);
    for(int i=0;i<s->nodes;i++) if(!s->fixed[i]) {
        Vec velocity=add(get(s->velocities,i),mul(get(w->acceleration,i),h*.5));
        put(s->velocities,i,velocity);put(s->positions,i,add(get(s->positions,i),mul(velocity,h)));
    }
    /* Add the constraint displacement to half-step velocity without rebuilding
       unconstrained velocities from x_new-x_old (which loses small increments). */
    double predicted[3*MAX_NODES];memcpy(predicted,s->positions,bytes);
    if(!project_positions(w)) return 0;
    for(int i=0;i<s->nodes;i++) if(!s->fixed[i])
        put(s->velocities,i,add(get(s->velocities,i),mul(sub(get(s->positions,i),get(predicted,i)),1/h)));
    if(!accelerations(w,t+.5*h)) return 0;
    for(int i=0;i<s->nodes;i++) if(!s->fixed[i])
        put(s->velocities,i,add(get(s->velocities,i),mul(get(w->acceleration,i),h*.5)));
    damping(w,h*.5,1);
    project_velocities(w);
    return 1;
}
static int diagnostics(Work *w,int initial) {
    ConnectionSimulation *s=w->s;ConnectionDiagnostics *d=w->d;double kinetic=0,potential=0;
    for(int i=0;i<s->nodes;i++) {
        Vec p=get(s->positions,i),velocity=get(s->velocities,i);
        if(!finite_vec(p)||!finite_vec(velocity)||fmax(fabs(p.x),fmax(fabs(p.y),fabs(p.z)))>1e9) return 0;
        double speed=length(velocity);d->max_speed_m_s=fmax(d->max_speed_m_s,speed);
        kinetic+=.5*s->mass[i]*dot(velocity,velocity);
    }
    for(int j=0;j<s->connections;j++) {
        Connection *e=&s->connection[j];Vec n;double error=geometry(w,j,&n)-e->rest;
        if(e->type==0) potential+=.5*e->stiffness*error*error;
        else {
            double residual=e->type==1?fabs(error):fmax(0,error);
            if(e->type==1) d->max_rod_error_m=fmax(d->max_rod_error_m,residual);
            else d->max_rope_extension_m=fmax(d->max_rope_extension_m,residual);
            d->max_constraint_error_ratio=fmax(d->max_constraint_error_ratio,residual/fmax(1e-6,1e-4*e->rest));
        }
    }
    if(!isfinite(kinetic)||!isfinite(potential)) return 0;
    if(initial) {d->initial_kinetic_energy=kinetic;d->initial_spring_energy=potential;}
    d->final_kinetic_energy=kinetic;d->final_spring_energy=potential;
    double initial_energy=d->initial_kinetic_energy+d->initial_spring_energy;
    double relative=fabs(kinetic+potential-initial_energy)/fmax(initial_energy,1e-12);
    if(!isfinite(relative)) return 0;
    d->connection_peak_relative_energy_drift=fmax(d->connection_peak_relative_energy_drift,relative);
    return 1;
}
static int observe(Work *w,double t) {
    ConnectionSimulation *s=w->s;if(!s->metrics) return 1;
    int row=w->d->observations_written;if(row>=s->observation_capacity) return 0;
    for(int j=0;j<s->metrics;j++) {
        ConnectionMetric *m=&s->metric[j];double result;
        if(m->type==0) result=s->positions[3*m->a+m->axis];
        else if(m->type==1) result=length(get(s->velocities,m->a));
        else if(m->type==2) result=length(sub(get(s->positions,m->a),get(s->positions,m->b)));
        else {
            Connection *e=&s->connection[m->a];Vec n;double len=geometry(w,m->a,&n),extension=len-e->rest;
            if(m->type==3) result=len;
            else if(m->type==4) result=extension;
            else if(m->type==5) result=e->stiffness*extension+e->damping*dot(sub(get(s->velocities,e->b),get(s->velocities,e->a)),n);
            else result=.5*e->stiffness*extension*extension;
        }
        if(!isfinite(result)) return 0;
        s->observation_values[(size_t)row*s->metrics+j]=result;
    }
    s->observation_times[row]=t;w->d->observations_written++;return 1;
}
static void frame(Work *w,double t,double blend) {
    ConnectionSimulation *s=w->s;int row=w->d->frames_written++;
    s->frame_times[row]=t;
    for(int i=0;i<3*s->nodes;i++)
        s->frames[(size_t)row*3*s->nodes+i]=w->macro_start[i]+blend*(s->positions[i]-w->macro_start[i]);
}
static int validate(ConnectionSimulation *s) {
    if(!s || s->abi!=CONNECTIONS_ABI || s->nodes<2 || s->nodes>MAX_NODES
       || s->connections<1 || s->connections>MAX_CONNECTIONS || s->fields<0 || s->fields>MAX_FIELDS
       || s->metrics<0 || s->metrics>16 || s->iterations<1 || s->iterations>64 || s->substeps<1 || s->substeps>64
       || s->frame_capacity<2 || s->frame_capacity>2401 || s->observation_capacity<0 || s->observation_capacity>250002
       || !(s->dt>0) || !(s->duration>0) || !(s->fps>0) || !isfinite(s->dt+s->duration+s->fps+s->deadline_seconds)
       || s->duration/s->dt>250000 || s->duration*s->fps>2400
       || !s->positions || !s->velocities || !s->mass || !s->fixed || !s->connection || !s->frames || !s->frame_times
       || (s->fields && (!s->field || !s->field_mask))
       || (s->metrics && (!s->metric || !s->observation_times || !s->observation_values))) return 0;
    int frames=(int)fmax(1,ceil(s->duration*s->fps-1e-10))+1;
    if(s->frame_capacity<frames || (s->metrics && s->observation_capacity<(int)ceil(s->duration/s->dt)+1)) return 0;
    double stiffness_sum[MAX_NODES]={0},damping_sum[MAX_NODES]={0};
    for(int i=0;i<s->nodes;i++) {
        if(!(s->mass[i]>=1e-12 && s->mass[i]<=1e12) || !isfinite(s->mass[i])
           || (s->fixed[i]!=0 && s->fixed[i]!=1) || !finite_vec(get(s->positions,i)) || !finite_vec(get(s->velocities,i))
           || (s->fixed[i] && length(get(s->velocities,i))!=0)
           || (s->fields && (s->field_mask[i]>>s->fields))) return 0;
    }
    for(int j=0;j<s->connections;j++) {
        Connection *e=&s->connection[j];
        if(e->type<0 || e->type>2 || e->a<0 || e->a>=s->nodes || e->b<0 || e->b>=s->nodes || e->a==e->b
           || (s->fixed[e->a] && s->fixed[e->b]) || !(e->rest>0) || e->rest>1e6
           || !isfinite(e->rest+e->stiffness+e->damping) || e->stiffness<0 || e->damping<0
           || (e->type==0 && !(e->stiffness>0)) || (e->type!=0 && (e->stiffness!=0 || e->damping!=0))) return 0;
        double len=length(sub(get(s->positions,e->a),get(s->positions,e->b)));
        if(!isfinite(len) || (e->type!=2 && len<=1e-9) || (e->type==1 && fabs(len-e->rest)>tolerance(e->rest))
           || (e->type==2 && len-e->rest>tolerance(e->rest))) return 0;
        if(e->type==1) {
            Vec va=get(s->velocities,e->a),vb=get(s->velocities,e->b);
            Vec delta=sub(get(s->positions,e->b),get(s->positions,e->a));
            double radial=dot(sub(vb,va),delta)/len;
            double speed_scale=fmax(1,fmax(length(va),length(vb)));
            if(!isfinite(radial+speed_scale) || fabs(radial)>fmax(1e-9,1e-7*speed_scale)) return 0;
        }
        if(e->type==0) {stiffness_sum[e->a]+=e->stiffness;stiffness_sum[e->b]+=e->stiffness;
            damping_sum[e->a]+=e->damping;damping_sum[e->b]+=e->damping;}
    }
    double omega_squared=0,damping_rate=0;
    for(int i=0;i<s->nodes;i++) if(!s->fixed[i]) {
        omega_squared=fmax(omega_squared,2*stiffness_sum[i]/s->mass[i]);
        damping_rate=fmax(damping_rate,2*damping_sum[i]/s->mass[i]);
    }
    if(!isfinite(omega_squared+damping_rate) || s->dt*sqrt(omega_squared)/s->substeps>.150000000001
       || s->dt*damping_rate/s->substeps>.250000000001) return 0;
    for(int j=0;j<s->fields;j++) {
        ConnectionField *f=&s->field[j];
        if(f->type<0 || f->type>2 || !finite_vec(get(f->origin,0)) || !finite_vec(get(f->vector,0))
           || !isfinite(f->strength+f->radius+f->secondary_strength+f->start_time+f->end_time)
           || f->start_time<0 || f->end_time<=f->start_time || (f->type!=0 && !(f->radius>0))
           || (f->type==2 && fabs(length(get(f->vector,0))-1)>1e-9)) return 0;
    }
    for(int j=0;j<s->metrics;j++) {
        ConnectionMetric *m=&s->metric[j];
        if(m->type<0 || m->type>6 || m->axis<0 || m->axis>2 || m->a<0
           || (m->type<3 && (m->a>=s->nodes || m->b<0 || m->b>=s->nodes))
           || (m->type>=3 && m->a>=s->connections)
           || (m->type>=5 && s->connection[m->a].type!=0)) return 0;
    }
    return 1;
}
CONNECTIONS_API uint32_t connections_abi_version(void) {return CONNECTIONS_ABI;}
CONNECTIONS_API int32_t connections_simulate(ConnectionSimulation *s,ConnectionDiagnostics *d) {
    if(!d) return 1;memset(d,0,sizeof(*d));d->status=1;
    if(!validate(s)) return 1;
    Work w={0};w.s=s;w.d=d;w.started=now();d->finite=1;d->status=0;
    for(int i=0;i<s->nodes;i++) w.inverse_mass[i]=s->fixed[i]?0:1/s->mass[i];
    for(int j=0;j<s->connections;j++) {Connection *e=&s->connection[j];Vec delta=sub(get(s->positions,e->b),get(s->positions,e->a));
        double len=length(delta);w.initial_direction[j]=len>1e-14?mul(delta,1/len):v(1,0,0);}
    memcpy(w.macro_start,s->positions,(size_t)3*s->nodes*sizeof(double));
    if(!diagnostics(&w,1) || !observe(&w,0)) {d->status=4;d->finite=0;goto done;}
    frame(&w,0,0);
    int target_frames=(int)fmax(1,ceil(s->duration*s->fps-1e-10))+1;
    double t=0;
    while(t<s->duration-1e-12) {
        if(s->duration-t<=fmax(1e-12,s->dt*1e-5)) {t=s->duration;break;}
        if(expired(&w)) {d->status=3;goto done;}
        double start=t,dt=fmin(s->dt,s->duration-t),end=start+dt;
        memcpy(w.macro_start,s->positions,(size_t)3*s->nodes*sizeof(double));
        int used=0;
        for(int substep=0;substep<s->substeps;substep++) {
            double nominal=substep+1==s->substeps?end:start+dt*(substep+1)/s->substeps;
            while(t<nominal) {
                if(expired(&w)) {d->status=3;goto done;}
                double next=nominal;
                for(int j=0;j<s->fields;j++) {
                    double a=s->field[j].start_time,b=s->field[j].end_time;
                    if(a>t && a<next) next=a;if(b>t && b<next) next=b;
                }
                double h=next-t;
                if(!(h>0)) {d->status=5;goto done;}
                if(!step(&w,t,h)||!diagnostics(&w,0)) {d->status=4;d->finite=0;goto done;}
                t=next;used++;d->substeps++;d->simulated_time_s=t;
            }
        }
        d->steps++;if(used>d->max_substeps_used)d->max_substeps_used=used;
        if(!observe(&w,t)) {d->status=4;d->finite=0;goto done;}
        while(d->frames_written<target_frames-1) {
            int row=d->frames_written;double ft=row/s->fps;
            if(ft>end+1e-12) break;
            frame(&w,ft,fmin(1,fmax(0,(ft-start)/dt)));
        }
    }
    if(d->frames_written!=target_frames-1) {d->status=5;goto done;}
    frame(&w,s->duration,1);
    d->completed=1;d->simulated_time_s=s->duration;
done:
    d->runtime_s=now()-w.started;return d->status;
}
