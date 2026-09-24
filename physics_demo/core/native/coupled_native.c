/* Partitioned shared-clock coupling. Existing fluid/surface solvers retain
 * their state. Each attachment/contact impulse is scattered to both owners.
 * This is not a monolithic moving-boundary DFSPH pressure solve. */
#define _POSIX_C_SOURCE 200809L
#include "coupled_native.h"
#include <float.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct { CoupledEndpoint e[3]; double weight[3]; int count; } Group;
typedef struct {
    CoupledSimulation *s; CoupledDiagnostics *d; PhyContext *particles; MeshContext *mesh;
    PhyDiagnostics *pd; MeshDiagnostics *md;
    rm_vec3 *old_particles,*old_points,*old_mesh,*initial_mesh;
    CoupledRigid *old_rigids; double started,particle_inverse_mass,feature;
} World;
static double now(void) {struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (double)t.tv_sec+1e-9*(double)t.tv_nsec;}
static double clamp(double x,double a,double b) {return fmin(b,fmax(a,x));}
static rm_vec3 get3(const double *p,int i) {return rm_v3(p[3*i],p[3*i+1],p[3*i+2]);}
static void put3(double *p,int i,rm_vec3 v) {p[3*i]=v.x;p[3*i+1]=v.y;p[3*i+2]=v.z;}
static double axis(rm_vec3 p,int i) {return i==0?p.x:i==1?p.y:p.z;}
static rm_vec3 unit(rm_vec3 p,rm_vec3 fallback) {double l=rm_norm(p);return l>1e-14?rm_scale(p,1/l):fallback;}
static rm_vec3 particle_position(PhySimulation *s,int i) {return rm_v3(s->x[i],s->y[i],s->z[i]);}
static rm_vec3 particle_velocity(PhySimulation *s,int i) {return rm_v3(s->vx[i],s->vy[i],s->vz[i]);}
static void particle_put(PhySimulation *s,int i,rm_vec3 p,rm_vec3 v) {s->x[i]=p.x;s->y[i]=p.y;s->z[i]=p.z;s->vx[i]=v.x;s->vy[i]=v.y;s->vz[i]=v.z;}
static void pivot_sync(CoupledRigid *r) {
    if(!r->pivot)return;
    rm_vec3 offset=rm_scale(rm_q_rotate(r->body.orientation,r->pivot_local),-1);
    r->body.position=rm_add(r->pivot_point,offset);
    r->body.velocity=rm_cross(rm_world_omega(r->body.orientation,r->body.angular_momentum,r->body.inertia_body_diag),offset);
}
static rm_vec3 position(World *w,CoupledEndpoint e) {
    if(e.kind==0)return w->s->point[e.index].position;
    if(e.kind==1)return get3(w->s->mesh->positions,e.index);
    if(e.kind==2) {CoupledRigid *r=&w->s->rigid[e.index];return rm_add(r->body.position,rm_q_rotate(r->body.orientation,e.local_point));}
    if(e.kind==3)return particle_position(w->s->particles,e.index);
    return e.local_point;
}
static rm_vec3 lever(World *w,CoupledEndpoint e) {CoupledRigid *r=&w->s->rigid[e.index];return rm_sub(position(w,e),r->pivot?r->pivot_point:r->body.position);}
static rm_vec3 velocity(World *w,CoupledEndpoint e) {
    if(e.kind==0)return w->s->point[e.index].velocity;
    if(e.kind==1)return get3(w->s->mesh->velocities,e.index);
    if(e.kind==2) {CoupledRigid *r=&w->s->rigid[e.index];if(r->fixed)return rm_v3(0,0,0);
        rm_vec3 spin=rm_cross(rm_world_omega(r->body.orientation,r->body.angular_momentum,r->body.inertia_body_diag),lever(w,e));
        return r->pivot?spin:rm_add(r->body.velocity,spin);}
    if(e.kind==3)return particle_velocity(w->s->particles,e.index);
    return rm_v3(0,0,0);
}
static double inverse(World *w,CoupledEndpoint e,rm_vec3 n) {
    if(e.kind==0)return w->s->point[e.index].inverse_mass;
    if(e.kind==1)return w->s->mesh->inverse_mass[e.index];
    if(e.kind==2) {CoupledRigid *r=&w->s->rigid[e.index];return r->fixed?0:rm_contact_inv_mass(&r->body,lever(w,e),n);}
    return e.kind==3?w->particle_inverse_mass:0;
}
static void impulse(World *w,CoupledEndpoint e,rm_vec3 j,int positional) {
    if(e.kind==2) {CoupledRigid *r=&w->s->rigid[e.index];rm_vec3 arm=lever(w,e),oldv=r->body.velocity;
        if(r->fixed) {if(!positional)w->d->support_impulse=rm_sub(w->d->support_impulse,j);return;}
        if(positional) {if(!rm_apply_position_impulse(&r->body,arm,j))w->d->status=4;}
        else rm_apply_impulse(&r->body,arm,j);
        pivot_sync(r);
        if(r->pivot && !positional)w->d->support_impulse=rm_add(w->d->support_impulse,rm_sub(rm_scale(rm_sub(r->body.velocity,oldv),r->mass),j));
        return;
    }
    double inv=inverse(w,e,rm_v3(1,0,0));
    if(inv==0) {if(!positional)w->d->support_impulse=rm_sub(w->d->support_impulse,j);return;}
    rm_vec3 p=position(w,e),v=velocity(w,e);
    if(positional)p=rm_add(p,rm_scale(j,inv));else v=rm_add(v,rm_scale(j,inv));
    if(e.kind==0) {w->s->point[e.index].position=p;w->s->point[e.index].velocity=v;}
    else if(e.kind==1) {put3(w->s->mesh->positions,e.index,p);put3(w->s->mesh->velocities,e.index,v);}
    else if(e.kind==3)particle_put(w->s->particles,e.index,p,v);
}
static Group single(CoupledEndpoint e) {Group g={0};g.e[0]=e;g.weight[0]=1;g.count=1;return g;}
static CoupledEndpoint endpoint(int kind,int index) {CoupledEndpoint e={kind,index,{0,0,0}};return e;}
static rm_vec3 group_position(World *w,Group g) {rm_vec3 p=rm_v3(0,0,0);for(int i=0;i<g.count;i++)p=rm_add(p,rm_scale(position(w,g.e[i]),g.weight[i]));return p;}
static rm_vec3 group_velocity(World *w,Group g) {rm_vec3 v=rm_v3(0,0,0);for(int i=0;i<g.count;i++)v=rm_add(v,rm_scale(velocity(w,g.e[i]),g.weight[i]));return v;}
static double group_inverse(World *w,Group g,rm_vec3 n) {double d=0;for(int i=0;i<g.count;i++)d+=g.weight[i]*g.weight[i]*inverse(w,g.e[i],n);return d;}
static void group_impulse(World *w,Group g,rm_vec3 j,int positional) {for(int i=0;i<g.count;i++)impulse(w,g.e[i],rm_scale(j,g.weight[i]),positional);}
static void contact(World *w,Group a,Group b,rm_vec3 n,double gap,double friction,double restitution) {
    if(gap>=0 || !isfinite(gap))return;
    w->d->max_penetration_m=fmax(w->d->max_penetration_m,-gap);
    double den=group_inverse(w,a,n)+group_inverse(w,b,n);if(den<=0)return;
    rm_vec3 correction=rm_scale(n,-gap/den);
    group_impulse(w,a,correction,1);group_impulse(w,b,rm_scale(correction,-1),1);
    double vn=rm_dot(rm_sub(group_velocity(w,a),group_velocity(w,b)),n),normal=0;
    if(vn<0) {normal=-(1+restitution)*vn/den;rm_vec3 j=rm_scale(n,normal);group_impulse(w,a,j,0);group_impulse(w,b,rm_scale(j,-1),0);}
    if(normal>0 && friction>0) {
        rm_vec3 v=rm_sub(group_velocity(w,a),group_velocity(w,b));v=rm_sub(v,rm_scale(n,rm_dot(v,n)));double speed=rm_norm(v);
        if(speed>1e-12) {rm_vec3 t=rm_scale(v,1/speed);double inv=group_inverse(w,a,t)+group_inverse(w,b,t);
            if(inv>0) {rm_vec3 j=rm_scale(t,-fmin(friction*normal,speed/inv));group_impulse(w,a,j,0);group_impulse(w,b,rm_scale(j,-1),0);}}
    }
    w->d->contact_count++;w->d->contact_impulse_norm+=normal;
}
/* Exact signed distances for the supported homogeneous body geometries. */
static double shape_distance(CoupledRigid *r,rm_vec3 p,rm_vec3 *normal) {
    rm_vec3 a=rm_q_inverse_rotate(r->body.orientation,rm_sub(p,r->body.position)),n;double distance;
    if(r->shape==0) {double l=rm_norm(a);n=unit(a,rm_v3(1,0,0));distance=l-r->radius;}
    else if(r->shape==1) {
        rm_vec3 half=rm_scale(r->size,.5),q=rm_v3(fabs(a.x)-half.x,fabs(a.y)-half.y,fabs(a.z)-half.z);
        rm_vec3 out=rm_v3(fmax(q.x,0),fmax(q.y,0),fmax(q.z,0));double l=rm_norm(out),inside=fmin(fmax(q.x,fmax(q.y,q.z)),0);distance=l+inside;
        if(l>1e-14)n=rm_v3(copysign(out.x/l,a.x),copysign(out.y/l,a.y),copysign(out.z/l,a.z));
        else if(q.x>=q.y && q.x>=q.z)n=rm_v3(copysign(1,a.x),0,0);
        else if(q.y>=q.z)n=rm_v3(0,copysign(1,a.y),0);else n=rm_v3(0,0,copysign(1,a.z));
    } else {
        double radial=hypot(a.x,a.z),dr=radial-r->radius,dy=fabs(a.y)-.5*r->height;
        double x=fmax(dr,0),y=fmax(dy,0),l=hypot(x,y);distance=l+fmin(fmax(dr,dy),0);
        rm_vec3 nr=radial>1e-14?rm_v3(a.x/radial,0,a.z/radial):rm_v3(1,0,0),ny=rm_v3(0,copysign(1,a.y),0);
        n=l>1e-14?rm_add(rm_scale(nr,x/l),rm_scale(ny,y/l)):(dr>=dy?nr:ny);
    }
    *normal=rm_q_rotate(r->body.orientation,n);return distance;
}
static void point_rigid(World *w,CoupledEndpoint a,double radius,int index,double friction) {
    CoupledRigid *r=&w->s->rigid[index];rm_vec3 p=position(w,a),n;double d=shape_distance(r,p,&n);
    if(d>=radius)return;
    CoupledEndpoint b=endpoint(2,index);b.local_point=rm_q_inverse_rotate(r->body.orientation,rm_sub(rm_sub(p,rm_scale(n,d)),r->body.position));
    if(a.kind==2 && radius>0)a.local_point=rm_sub(a.local_point,rm_q_inverse_rotate(w->s->rigid[a.index].body.orientation,rm_scale(n,radius)));
    contact(w,single(a),single(b),n,d-radius,friction,r->restitution);
}
static void point_link(World *w,CoupledEndpoint point,double radius,int index) {
    CoupledLink *l=&w->s->link[index];if(l->radius<=0)return;
    if((point.kind==l->a.kind && point.index==l->a.index)||(point.kind==l->b.kind && point.index==l->b.index))return;
    rm_vec3 a=position(w,l->a),b=position(w,l->b),p=position(w,point),ab=rm_sub(b,a);double d=rm_dot(ab,ab);
    double t=d>1e-24?clamp(rm_dot(rm_sub(p,a),ab)/d,0,1):.5;
    rm_vec3 delta=rm_sub(p,rm_add(a,rm_scale(ab,t)));double length=rm_norm(delta);
    if(length>=radius+l->radius)return;
    Group g={0};g.count=2;g.e[0]=l->a;g.e[1]=l->b;g.weight[0]=1-t;g.weight[1]=t;
    /* A centre-line capsule has no spin degree of freedom: normal contact only. */
    contact(w,single(point),g,unit(delta,rm_v3(0,1,0)),length-radius-l->radius,0,0);
}
static double collider_distance(PhyCollider *c,rm_vec3 p,rm_vec3 *normal) {
    if(c->type==0) {*normal=get3(c->a,0);return rm_dot(p,*normal)-c->b[0];}
    if(c->type==3) {rm_vec3 start=get3(c->a,0),ab=rm_sub(get3(c->b,0),start);double den=rm_dot(ab,ab);
        rm_vec3 delta=rm_sub(p,rm_add(start,rm_scale(ab,clamp(rm_dot(rm_sub(p,start),ab)/den,0,1))));
        *normal=unit(delta,rm_v3(1,0,0));return rm_norm(delta)-c->c[0];}
    CoupledRigid r={0};r.body.position=get3(c->a,0);r.body.orientation=rm_q_identity();
    r.shape=c->type==1?0:1;r.radius=c->b[0];r.size=get3(c->b,0);return shape_distance(&r,p,normal);
}
static void point_static(World *w,CoupledEndpoint a,double radius) {
    for(int i=0;i<w->s->colliders;i++) {PhyCollider *c=&w->s->collider[i];rm_vec3 p=position(w,a),n;double d=collider_distance(c,p,&n);
        CoupledEndpoint fixed=endpoint(4,0);fixed.local_point=rm_sub(p,rm_scale(n,d));
        CoupledEndpoint surface=a;
        /* A sphere's centre is a collision sample, not its friction application
         * point. Use its physical surface lever arm for tangential impulses. */
        if(a.kind==2 && radius>0)surface.local_point=rm_sub(a.local_point,
            rm_q_inverse_rotate(w->s->rigid[a.index].body.orientation,rm_scale(n,radius)));
        contact(w,single(surface),single(fixed),n,d-radius,a.kind==2?c->friction:0,a.kind==2?w->s->rigid[a.index].restitution:0);
    }
}
/* A finite straight-capsule proxy. Every sample scatters to its two endpoints;
 * samples have no independent mass or spin. Plane contact is exact along the
 * straight axis; curved/thin obstacles have the documented 65-sample limit. */
static void link_support_contacts(World *w,int index) {
    CoupledLink *link=&w->s->link[index];if(link->radius<=0)return;
    double length=rm_norm(rm_sub(position(w,link->b),position(w,link->a)));
    int segments=(int)fmin(64,fmax(1,ceil(length/(2*link->radius))));
    for(int k=0;k<=segments;k++) {
        if((k&15)==0&&now()-w->started>=w->s->deadline_seconds){w->d->status=3;return;}
        Group a={0};a.count=2;a.e[0]=link->a;a.e[1]=link->b;a.weight[1]=(double)k/segments;a.weight[0]=1-a.weight[1];
        for(int j=0;j<w->s->colliders;j++) {rm_vec3 p=group_position(w,a),n;double d=collider_distance(&w->s->collider[j],p,&n);
            CoupledEndpoint b=endpoint(4,0);b.local_point=rm_sub(p,rm_scale(n,d));
            contact(w,a,single(b),n,d-link->radius,0,0);}
        for(int j=0;j<w->s->rigids;j++) {
            /* An attached rigid is part of the same mechanism: never collide
             * its attached capsule against that same owner's geometry. */
            if((link->a.kind==2&&link->a.index==j)||(link->b.kind==2&&link->b.index==j))continue;
            CoupledRigid *r=&w->s->rigid[j];rm_vec3 p=group_position(w,a),n;double d=shape_distance(r,p,&n);
            if(d>=link->radius)continue;
            CoupledEndpoint b=endpoint(2,j);b.local_point=rm_q_inverse_rotate(r->body.orientation,rm_sub(rm_sub(p,rm_scale(n,d)),r->body.position));
            contact(w,a,single(b),n,d-link->radius,0,r->restitution);
        }
    }
}
static rm_vec3 closest_triangle(rm_vec3 p,rm_vec3 a,rm_vec3 b,rm_vec3 c,double weight[3]) {
    rm_vec3 ab=rm_sub(b,a),ac=rm_sub(c,a),ap=rm_sub(p,a);double d1=rm_dot(ab,ap),d2=rm_dot(ac,ap);
    double scale=fmax(rm_dot(ab,ab),rm_dot(ac,ac));rm_vec3 normal=rm_cross(ab,ac);
    if(rm_dot(normal,normal)<=1e-24*scale*scale) {
        rm_vec3 vertices[3]={a,b,c},best=a;double minimum=INFINITY;
        for(int i=0;i<3;i++) {int j=(i+1)%3;rm_vec3 edge=rm_sub(vertices[j],vertices[i]);double den=rm_dot(edge,edge);
            double t=den>0?clamp(rm_dot(rm_sub(p,vertices[i]),edge)/den,0,1):0;
            rm_vec3 q=rm_add(vertices[i],rm_scale(edge,t));double distance=rm_norm(rm_sub(p,q));
            if(distance<minimum) {minimum=distance;best=q;weight[0]=weight[1]=weight[2]=0;weight[i]=1-t;weight[j]=t;}}
        return best;
    }
    if(d1<=0&&d2<=0){weight[0]=1;weight[1]=weight[2]=0;return a;}
    rm_vec3 bp=rm_sub(p,b);double d3=rm_dot(ab,bp),d4=rm_dot(ac,bp);
    if(d3>=0&&d4<=d3){weight[1]=1;weight[0]=weight[2]=0;return b;}
    double vc=d1*d4-d3*d2;
    if(vc<=0&&d1>=0&&d3<=0){double t=d1/(d1-d3);weight[0]=1-t;weight[1]=t;weight[2]=0;return rm_add(a,rm_scale(ab,t));}
    rm_vec3 cp=rm_sub(p,c);double d5=rm_dot(ab,cp),d6=rm_dot(ac,cp);
    if(d6>=0&&d5<=d6){weight[2]=1;weight[0]=weight[1]=0;return c;}
    double vb=d5*d2-d1*d6;
    if(vb<=0&&d2>=0&&d6<=0){double t=d2/(d2-d6);weight[0]=1-t;weight[1]=0;weight[2]=t;return rm_add(a,rm_scale(ac,t));}
    double va=d3*d6-d5*d4;
    if(va<=0&&(d4-d3)>=0&&(d5-d6)>=0){double t=(d4-d3)/((d4-d3)+(d5-d6));weight[0]=0;weight[1]=1-t;weight[2]=t;return rm_add(b,rm_scale(rm_sub(c,b),t));}
    double inverse=1/(va+vb+vc);weight[1]=vb*inverse;weight[2]=vc*inverse;weight[0]=1-weight[1]-weight[2];
    return rm_add(a,rm_add(rm_scale(ab,weight[1]),rm_scale(ac,weight[2])));
}
static int outside_triangle_aabb(rm_vec3 p,rm_vec3 a,rm_vec3 b,rm_vec3 c,double reach) {
    /* Leave exceptional values to the exact path and its existing error checks.
     * The guard covers rounding in the bounds, sample and closest-point math. */
    double scale=fabs(p.x)+fabs(p.y)+fabs(p.z)+fabs(a.x)+fabs(a.y)+fabs(a.z)
                +fabs(b.x)+fabs(b.y)+fabs(b.z)+fabs(c.x)+fabs(c.y)+fabs(c.z)+fabs(reach);
    if(!(scale<=1e50) || reach<0)return 0;
    double extent=reach+64*DBL_EPSILON*(scale+1);
    return p.x<fmin(a.x,fmin(b.x,c.x))-extent || p.x>fmax(a.x,fmax(b.x,c.x))+extent
        || p.y<fmin(a.y,fmin(b.y,c.y))-extent || p.y>fmax(a.y,fmax(b.y,c.y))+extent
        || p.z<fmin(a.z,fmin(b.z,c.z))-extent || p.z>fmax(a.z,fmax(b.z,c.z))+extent;
}
static void rigid_mesh_sample(World *w,CoupledEndpoint sample,double radius) {
    MeshSimulation *s=w->s->mesh;if(!s)return;
    rm_vec3 p=position(w,sample);
    for(int o=0;o<s->objects;o++) {MeshObject *object=&s->object[o];
        double reach=radius+object->thickness;
        for(int triangle=object->triangle_start;triangle<object->triangle_start+object->triangle_count;triangle++) {
            if((triangle&127)==0&&now()-w->started>=w->s->deadline_seconds){w->d->status=3;return;}
            int *ids=&s->indices[3*triangle];rm_vec3 a=get3(s->positions,ids[0]),b=get3(s->positions,ids[1]),c=get3(s->positions,ids[2]);
            if(outside_triangle_aabb(p,a,b,c,reach))continue;
            double weights[3];rm_vec3 q=closest_triangle(p,a,b,c,weights),delta=rm_sub(p,q);double distance=rm_norm(delta);
            if(distance>=reach)continue;
            Group surface={0};surface.count=3;
            for(int k=0;k<3;k++){surface.e[k]=endpoint(1,ids[k]);surface.weight[k]=weights[k];}
            rm_vec3 normal=unit(delta,unit(rm_cross(rm_sub(b,a),rm_sub(c,a)),rm_v3(0,1,0)));
            if(distance<1e-14&&rm_dot(rm_sub(velocity(w,sample),group_velocity(w,surface)),normal)>0)normal=rm_scale(normal,-1);
            CoupledRigid *r=&w->s->rigid[sample.index];CoupledEndpoint touching=sample;
            if(radius>0)touching.local_point=rm_sub(sample.local_point,rm_q_inverse_rotate(r->body.orientation,rm_scale(normal,radius)));
            int contacts_before=w->d->contact_count;
            contact(w,single(touching),surface,normal,distance-radius-object->thickness,
                    sqrt(r->friction*object->friction),r->restitution);
            if(w->d->contact_count!=contacts_before)p=position(w,sample);
        }
    }
}
static int mesh_point(World *w,CoupledEndpoint e,double radius,double h) {
    rm_vec3 p=position(w,e),v=velocity(w,e);double pos[3]={p.x,p.y,p.z},vel[3]={v.x,v.y,v.z};int32_t count=0;
    int code=mesh_context_contact_point(w->mesh,pos,vel,inverse(w,e,rm_v3(1,0,0)),radius,h,0,&count);
    if(code)return code;
    p=get3(pos,0);v=get3(vel,0);
    if(e.kind==0) {w->s->point[e.index].position=p;w->s->point[e.index].velocity=v;}
    else particle_put(w->s->particles,e.index,p,v);
    w->d->contact_count+=count;return 0;
}
static int contacts(World *w,double h) {
    CoupledSimulation *s=w->s;int status=0;
    if(w->mesh && (status=mesh_context_refit(w->mesh)))return status;
    if(s->particles)for(int i=0;i<s->particles->particle_count;i++) {
        if((i&63)==0 && now()-w->started>=s->deadline_seconds)return 3;
        CoupledEndpoint e=endpoint(3,i);double radius=.45*s->particles->spacing;
        for(int j=0;j<s->rigids;j++)point_rigid(w,e,radius,j,s->friction);
        for(int j=0;j<s->points;j++)if(s->point[j].radius>0) {rm_vec3 delta=rm_sub(position(w,e),s->point[j].position);double l=rm_norm(delta);contact(w,single(e),single(endpoint(0,j)),unit(delta,rm_v3(1,0,0)),l-radius-s->point[j].radius,0,0);}
        for(int j=0;j<s->links;j++)point_link(w,e,radius,j);
        if(w->mesh && (status=mesh_point(w,e,radius,h)))return status;
    }
    for(int i=0;i<s->points;i++)if(s->point[i].radius>0) {
        CoupledEndpoint e=endpoint(0,i);double radius=s->point[i].radius;
        point_static(w,e,radius);
        for(int j=0;j<s->rigids;j++)point_rigid(w,e,radius,j,0);
        for(int j=i+1;j<s->points;j++)if(s->point[j].radius>0) {rm_vec3 delta=rm_sub(position(w,e),s->point[j].position);double l=rm_norm(delta);contact(w,single(e),single(endpoint(0,j)),unit(delta,rm_v3(1,0,0)),l-radius-s->point[j].radius,0,0);}
        for(int j=0;j<s->links;j++)point_link(w,e,radius,j);
        if(w->mesh && (status=mesh_point(w,e,radius,h)))return status;
    }
    if(s->mesh)for(int o=0;o<s->mesh->objects;o++) {MeshObject *object=&s->mesh->object[o];
        for(int i=object->start;i<object->start+object->count;i++) {CoupledEndpoint e=endpoint(1,i);
            for(int j=0;j<s->rigids;j++)point_rigid(w,e,object->thickness,j,s->friction);
            for(int j=0;j<s->links;j++)point_link(w,e,object->thickness,j);}}
    for(int i=0;i<s->links;i++){link_support_contacts(w,i);if(w->d->status)return w->d->status;}
    /* Support/contact samples are geometric, never extra physical masses. */
    for(int i=0;i<s->rigids;i++) {CoupledRigid *r=&s->rigid[i];
        int count=r->shape==0?1:r->shape==1?8:24;
        for(int k=0;k<count;k++) {CoupledEndpoint e=endpoint(2,i);double radius=0;
            if(r->shape==0)radius=r->radius;
            else if(r->shape==1)e.local_point=rm_v3((k&1?.5:-.5)*r->size.x,(k&2?.5:-.5)*r->size.y,(k&4?.5:-.5)*r->size.z);
            else {double angle=6.283185307179586*(k%12)/12.;e.local_point=rm_v3(r->radius*cos(angle),(k<12?-.5:.5)*r->height,r->radius*sin(angle));}
            if(!r->fixed) {
                point_static(w,e,radius);
                for(int j=0;j<s->rigids;j++)if(j!=i)point_rigid(w,e,radius,j,s->friction);
            }
            rigid_mesh_sample(w,e,radius);
            if(w->d->status)return w->d->status;
        }
    }
    return w->d->status;
}
static int links(World *w,double h,int damping,int reverse) {
    for(int j=0;j<w->s->links;j++) {CoupledLink *l=&w->s->link[reverse?w->s->links-1-j:j];if(l->type!=0)continue;
        rm_vec3 delta=rm_sub(position(w,l->b),position(w,l->a));double length=rm_norm(delta);if(length<=1e-12)return 4;
        rm_vec3 n=rm_scale(delta,1/length);double inv=inverse(w,l->a,n)+inverse(w,l->b,n),magnitude;
        if(inv<=0)continue;
        if(damping) {double speed=rm_dot(rm_sub(velocity(w,l->b),velocity(w,l->a)),n);magnitude=speed*(-expm1(-l->damping*inv*h))/inv;}
        else magnitude=l->stiffness*(length-l->rest)*h;
        rm_vec3 force=rm_scale(n,magnitude);impulse(w,l->a,force,0);impulse(w,l->b,rm_scale(force,-1),0);w->d->attachment_impulse_norm+=fabs(magnitude);
    }return w->d->status;
}
static void link_constraints(World *w) {
    for(int i=0;i<w->s->links;i++) {CoupledLink *l=&w->s->link[i];if(l->type==0)continue;
        rm_vec3 delta=rm_sub(position(w,l->b),position(w,l->a));double length=rm_norm(delta);if(length<=1e-14)continue;
        rm_vec3 n=rm_scale(delta,1/length);double error=length-l->rest,inv=inverse(w,l->a,n)+inverse(w,l->b,n);if(inv<=0)continue;
        if(l->type==1 || error>0) {rm_vec3 j=rm_scale(n,error/inv);impulse(w,l->a,j,1);impulse(w,l->b,rm_scale(j,-1),1);}
        double speed=rm_dot(rm_sub(velocity(w,l->b),velocity(w,l->a)),n);
        if(l->type==1 || (error>=-1e-8 && speed>0)) {rm_vec3 j=rm_scale(n,speed/inv);impulse(w,l->a,j,0);impulse(w,l->b,rm_scale(j,-1),0);}
    }
}
static rm_vec3 field_acceleration(World *w,CoupledPoint *p,double t) {
    rm_vec3 out=rm_v3(0,0,0);
    for(int i=0;i<w->s->fields;i++) {PhyForceField *f=&w->s->field[i];if(!(p->field_mask&(1u<<i)) || t<f->start_time || t>=f->end_time)continue;
        if(f->type==0)out=rm_add(out,get3(f->vector,0));
        else {rm_vec3 delta=rm_sub(p->position,get3(f->origin,0));if(f->type==2)delta=rm_sub(delta,rm_scale(get3(f->vector,0),rm_dot(delta,get3(f->vector,0))));
            double r=rm_norm(delta);if(r>1e-12 && r<f->radius) {rm_vec3 radial=rm_scale(delta,1/r);double falloff=1-r/f->radius;
                rm_vec3 a=f->type==1?rm_scale(radial,f->strength):rm_sub(rm_scale(rm_cross(get3(f->vector,0),radial),f->strength),rm_scale(radial,f->secondary_strength));out=rm_add(out,rm_scale(a,falloff));}}
    }return out;
}
static int advance(World *w,double t,double h) {
    CoupledSimulation *s=w->s;int code=links(w,.5*h,1,0);if(code)return code;
    if((code=links(w,.5*h,0,0)))return code;
    for(int i=0;i<s->points;i++) {CoupledPoint *p=&s->point[i];if(p->inverse_mass<=0)continue;rm_vec3 a=field_acceleration(w,p,t+.5*h);
        p->velocity=rm_add(p->velocity,rm_scale(a,.5*h));p->position=rm_add(p->position,rm_scale(p->velocity,h));
        p->velocity=rm_add(p->velocity,rm_scale(field_acceleration(w,p,t+.5*h),.5*h));}
    for(int i=0;i<s->rigids;i++) {CoupledRigid *r=&s->rigid[i];if(r->fixed)continue;
        if(r->pivot) {if(!rm_step_pivot(&r->body,r->pivot_point,rm_scale(r->pivot_local,-1),s->gravity,r->mass,h))return 4;}
        else {r->body.velocity=rm_add(r->body.velocity,rm_scale(s->gravity,.5*h));r->body.position=rm_add(r->body.position,rm_scale(r->body.velocity,h));
            if(!rm_step_orientation(&r->body.orientation,r->body.angular_momentum,r->body.inertia_body_diag,h))return 4;
            r->body.velocity=rm_add(r->body.velocity,rm_scale(s->gravity,.5*h));}}
    if(w->particles && (code=phy_context_step(w->particles,t,h)))return code;
    if(w->mesh && (code=mesh_context_step(w->mesh,h,w->md)))return code;
    for(int i=0;i<s->iterations;i++) {link_constraints(w);if((code=contacts(w,h)))return code;
        if(w->mesh && (code=mesh_context_project(w->mesh,h,w->md)))return code;}
    if((code=links(w,.5*h,0,1)))return code;
    return links(w,.5*h,1,1);
}
static rm_vec3 entity_center(World *w,int index,int speeds) {
    CoupledEntity *e=&w->s->entity[index];rm_vec3 sum=rm_v3(0,0,0);
    if(e->kind==1)return speeds?w->s->point[e->start].velocity:w->s->point[e->start].position;
    if(e->kind==3)return speeds?w->s->rigid[e->start].body.velocity:w->s->rigid[e->start].body.position;
    int start=e->kind==2?w->s->mesh->object[e->start].start:e->start;
    for(int i=start;i<start+e->count;i++)sum=rm_add(sum,e->kind==0?(speeds?particle_velocity(w->s->particles,i):particle_position(w->s->particles,i)):get3(speeds?w->s->mesh->velocities:w->s->mesh->positions,i));
    return rm_scale(sum,1./e->count);
}
static double mesh_volume(MeshSimulation *s,MeshObject *o) {
    double value=0;rm_vec3 origin=get3(s->positions,o->start);
    for(int i=o->triangle_start;i<o->triangle_start+o->triangle_count;i++) {int *ids=&s->indices[3*i];value+=rm_dot(rm_sub(get3(s->positions,ids[0]),origin),rm_cross(rm_sub(get3(s->positions,ids[1]),origin),rm_sub(get3(s->positions,ids[2]),origin)))/6;}
    return value;
}
static double metric(World *w,CoupledMetric *m) {
    CoupledSimulation *s=w->s;
    if(m->type>=7 && m->type<=10) {CoupledLink *l=&s->link[m->a];rm_vec3 d=rm_sub(position(w,l->b),position(w,l->a));double len=rm_norm(d),ext=len-l->rest;
        if(m->type==7)return len;if(m->type==8)return ext;if(m->type==10)return .5*l->stiffness*ext*ext;
        return l->stiffness*ext+l->damping*rm_dot(rm_sub(velocity(w,l->b),velocity(w,l->a)),unit(d,rm_v3(0,0,0)));}
    CoupledEntity *e=&s->entity[m->a];
    if(m->type>=11) {CoupledRigid *r=&s->rigid[e->start];
        if(m->type==11)return rm_norm(rm_world_omega(r->body.orientation,r->body.angular_momentum,r->body.inertia_body_diag));
        if(m->type==12)return axis(r->body.angular_momentum,m->axis0);
        if(m->type==13)return rm_rotational_energy(r->body.orientation,r->body.angular_momentum,r->body.inertia_body_diag);
        return acos(clamp(rm_q_rotate(r->body.orientation,rm_v3(0,1,0)).y,-1,1));}
    if(m->type==1)return axis(entity_center(w,m->a,0),m->axis0);
    if(m->type==2)return rm_norm(entity_center(w,m->a,1));
    if(m->type==3)return rm_norm(rm_sub(entity_center(w,m->a,0),entity_center(w,m->b,0)));
    int start=e->kind==2?s->mesh->object[e->start].start:e->start;double value=0;
    if(m->type==4)return mesh_volume(s->mesh,&s->mesh->object[e->start])/s->mesh->object[e->start].rest_volume;
    if(m->type==6) {for(int i=0;i<s->mesh->edges;i++) {MeshEdge *edge=&s->mesh->edge[i];if(!edge->bending && edge->a>=start && edge->a<start+e->count)value=fmax(value,fabs(rm_norm(rm_sub(get3(s->mesh->positions,edge->a),get3(s->mesh->positions,edge->b)))/edge->rest-1));}return value;}
    for(int i=start;i<start+e->count;i++) {rm_vec3 p=e->kind==0?particle_position(s->particles,i):get3(s->mesh->positions,i);
        if(m->type==5)value=fmax(value,rm_norm(rm_sub(p,w->initial_mesh[i])));
        else {rm_vec3 d=rm_sub(p,m->origin);value=fmax(value,hypot(axis(d,m->axis0),axis(d,m->axis1)));}}
    return value;
}
static void observe(World *w,double t) {if(!w->s->metrics)return;int row=w->d->observations_written++;w->s->observation_times[row]=t;for(int i=0;i<w->s->metrics;i++)w->s->observation_values[row*w->s->metrics+i]=metric(w,&w->s->metric[i]);}
static rm_vec3 lerp(rm_vec3 a,rm_vec3 b,double t) {return rm_add(a,rm_scale(rm_sub(b,a),t));}
static void frame(World *w,double t,double blend) {
    CoupledSimulation *s=w->s;int row=w->d->frames_written++;s->frame_times[row]=t;
    if(s->particles)for(int j=0;j<s->particles->render_count;j++) {int i=s->particles->render_indices[j];rm_vec3 p=lerp(w->old_particles[i],particle_position(s->particles,i),blend);float *out=s->particles->frame_particles+3*(row*s->particles->render_count+j);out[0]=(float)p.x;out[1]=(float)p.y;out[2]=(float)p.z;}
    for(int i=0;i<s->points;i++)put3(s->point_frames,row*s->points+i,lerp(w->old_points[i],s->point[i].position,blend));
    if(s->mesh)for(int i=0;i<s->mesh->vertices;i++) {rm_vec3 p=lerp(w->old_mesh[i],get3(s->mesh->positions,i),blend);float *out=s->mesh->frames+3*(row*s->mesh->vertices+i);out[0]=(float)p.x;out[1]=(float)p.y;out[2]=(float)p.z;}
    for(int i=0;i<s->rigids;i++) {CoupledRigid *r=&s->rigid[i];rm_quat a=w->old_rigids[i].body.orientation,b=r->body.orientation;
        double sign=a.w*b.w+a.x*b.x+a.y*b.y+a.z*b.z<0?-1:1;rm_quat q={a.w+(sign*b.w-a.w)*blend,a.x+(sign*b.x-a.x)*blend,a.y+(sign*b.y-a.y)*blend,a.z+(sign*b.z-a.z)*blend};q=rm_q_normalize(q);
        rm_vec3 p=lerp(w->old_rigids[i].body.position,r->body.position,blend);if(r->pivot)p=rm_sub(r->pivot_point,rm_q_rotate(q,r->pivot_local));
        double *out=s->rigid_frames+7*(row*s->rigids+i);out[0]=p.x;out[1]=p.y;out[2]=p.z;out[3]=q.w;out[4]=q.x;out[5]=q.y;out[6]=q.z;
    }
}
static void snapshot(World *w) {
    CoupledSimulation *s=w->s;if(s->particles)for(int i=0;i<s->particles->particle_count;i++)w->old_particles[i]=particle_position(s->particles,i);
    if(s->mesh)for(int i=0;i<s->mesh->vertices;i++)w->old_mesh[i]=get3(s->mesh->positions,i);
    for(int i=0;i<s->points;i++)w->old_points[i]=s->point[i].position;
    memcpy(w->old_rigids,s->rigid,(size_t)s->rigids*sizeof(CoupledRigid));
}
static rm_vec3 momentum(World *w) {
    CoupledSimulation *s=w->s;rm_vec3 p=rm_v3(0,0,0);
    if(s->particles)for(int i=0;i<s->particles->particle_count;i++)p=rm_add(p,rm_scale(particle_velocity(s->particles,i),1/w->particle_inverse_mass));
    if(s->mesh)for(int i=0;i<s->mesh->vertices;i++)if(s->mesh->inverse_mass[i]>0)p=rm_add(p,rm_scale(get3(s->mesh->velocities,i),1/s->mesh->inverse_mass[i]));
    for(int i=0;i<s->points;i++)if(s->point[i].inverse_mass>0)p=rm_add(p,rm_scale(s->point[i].velocity,1/s->point[i].inverse_mass));
    for(int i=0;i<s->rigids;i++)if(!s->rigid[i].fixed)p=rm_add(p,rm_scale(s->rigid[i].body.velocity,s->rigid[i].mass));
    return p;
}
static double speed(World *w) {
    CoupledSimulation *s=w->s;double value=0;
    if(s->particles)for(int i=0;i<s->particles->particle_count;i++)value=fmax(value,rm_norm(particle_velocity(s->particles,i)));
    if(s->mesh)for(int i=0;i<s->mesh->vertices;i++)value=fmax(value,rm_norm(get3(s->mesh->velocities,i)));
    for(int i=0;i<s->points;i++)value=fmax(value,rm_norm(s->point[i].velocity));
    for(int i=0;i<s->rigids;i++) {CoupledRigid *r=&s->rigid[i];double bound=r->shape==1?.5*rm_norm(r->size):hypot(r->radius,.5*r->height);
        double angular=rm_norm(rm_world_omega(r->body.orientation,r->body.angular_momentum,r->body.inertia_body_diag));value=fmax(value,rm_norm(r->body.velocity)+(s->particles||s->mesh||s->colliders||s->links||s->rigids>1?angular*bound:0));}
    return value;
}
static int finite_state(World *w) {
    CoupledSimulation *s=w->s;
    if(s->particles)for(int i=0;i<s->particles->particle_count;i++)if(!rm_vec_finite(particle_position(s->particles,i))||!rm_vec_finite(particle_velocity(s->particles,i)))return 0;
    if(s->mesh)for(int i=0;i<s->mesh->vertices;i++)if(!rm_vec_finite(get3(s->mesh->positions,i))||!rm_vec_finite(get3(s->mesh->velocities,i)))return 0;
    for(int i=0;i<s->points;i++)if(!rm_vec_finite(s->point[i].position)||!rm_vec_finite(s->point[i].velocity))return 0;
    for(int i=0;i<s->rigids;i++) {rm_body *r=&s->rigid[i].body;if(!rm_vec_finite(r->position)||!rm_vec_finite(r->velocity)||!rm_vec_finite(r->angular_momentum)||!rm_q_valid(r->orientation))return 0;w->d->max_quaternion_error=fmax(w->d->max_quaternion_error,fabs(rm_q_norm(r->orientation)-1));}
    for(int i=0;i<s->links;i++) {CoupledLink *l=&s->link[i];if(l->type) {double error=rm_norm(rm_sub(position(w,l->a),position(w,l->b)))-l->rest;w->d->max_link_constraint_error=fmax(w->d->max_link_constraint_error,l->type==1?fabs(error):fmax(error,0));}}
    return 1;
}
static int valid_endpoint(CoupledSimulation *s,CoupledEndpoint e) {return rm_vec_finite(e.local_point)&&e.index>=0&&((e.kind==0&&e.index<s->points)||(e.kind==1&&s->mesh&&e.index<s->mesh->vertices)||(e.kind==2&&e.index<s->rigids));}
static double endpoint_inverse_bound(World *w,CoupledEndpoint e) {
    if(e.kind!=2)return inverse(w,e,rm_v3(1,0,0));
    CoupledRigid *r=&w->s->rigid[e.index];if(r->fixed)return 0;
    rm_vec3 arm=r->pivot?rm_sub(e.local_point,r->pivot_local):e.local_point;
    rm_vec3 i=r->body.inertia_body_diag;
    return r->body.inv_mass+rm_dot(arm,arm)/fmin(i.x,fmin(i.y,i.z));
}
static int validate_resolution(World *w) {
    double omega2=0,gamma=0,h=w->s->dt/w->s->substeps;
    for(int i=0;i<w->s->links;i++) {CoupledLink *l=&w->s->link[i];
        double inv=endpoint_inverse_bound(w,l->a)+endpoint_inverse_bound(w,l->b);
        double length=rm_norm(rm_sub(position(w,l->b),position(w,l->a)));
        if(inv<=0||!isfinite(inv+length)||(l->type!=2&&length<=1e-9))return 0;
        double tolerance=fmax(1e-9,1e-7*l->rest);
        if((l->type==1&&fabs(length-l->rest)>tolerance)||(l->type==2&&length-l->rest>tolerance))return 0;
        if(l->type==0){omega2+=2*l->stiffness*inv;gamma+=2*l->damping*inv;}}
    return isfinite(omega2+gamma)&&h*sqrt(omega2)<=.050000000001&&h*gamma<=.250000000001;
}
static int validate(CoupledSimulation *s) {
    if(!s||s->abi!=COUPLED_ABI||s->points<0||s->points>64||s->rigids<0||s->rigids>16||s->links<0||s->links>128||s->entities<1||s->entities>128||s->metrics<0||s->metrics>16||s->fields<0||s->fields>8||s->colliders<0||s->colliders>64||s->substeps<1||s->substeps>64||s->iterations<1||s->iterations>16||s->frame_capacity<2||s->frame_capacity>2401||s->observation_capacity<0||s->observation_capacity>100002)return 0;
    if(!s->point||!s->rigid||!s->link||!s->entity||!s->metric||!s->field||!s->collider||!s->point_frames||!s->rigid_frames||!s->frame_times||!s->observation_times||!s->observation_values)return 0;
    if(!(s->dt>0)||!(s->duration>0)||!(s->fps>0)||!isfinite(s->dt+s->duration+s->fps+s->deadline_seconds+s->friction)||s->friction<0||s->friction>5||!rm_vec_finite(s->gravity)||s->duration/s->dt>100000||s->duration*s->fps>2400)return 0;
    if(s->frame_capacity<(int)fmax(1,ceil(s->duration*s->fps-1e-10))+1||(s->metrics&&s->observation_capacity<(int)ceil(s->duration/s->dt)+1))return 0;
    if(s->particles&&(s->particles->particle_count<0||s->particles->particle_count>4096||s->particles->frame_capacity<s->frame_capacity||s->particles->duration!=s->duration||s->particles->body_count!=0))return 0;
    if(s->mesh&&(s->mesh->vertices<3||s->mesh->vertices>2048||s->mesh->objects<1||s->mesh->objects>16||!s->mesh->object||s->mesh->frame_capacity<s->frame_capacity||s->mesh->duration!=s->duration))return 0;
    for(int i=0;i<s->fields;i++) {PhyForceField *f=&s->field[i];
        if(f->type<0||f->type>2||!rm_vec_finite(get3(f->origin,0))||!rm_vec_finite(get3(f->vector,0))||
            !isfinite(f->strength+f->radius+f->secondary_strength+f->start_time+f->end_time)||f->start_time<0||
            !(f->end_time>f->start_time)||f->end_time>s->duration||(f->type!=0&&!(f->radius>0))||
            (f->type==2&&fabs(rm_norm(get3(f->vector,0))-1)>1e-9))return 0;}
    for(int i=0;i<s->colliders;i++) {PhyCollider *c=&s->collider[i];
        if(c->type<0||c->type>3||!rm_vec_finite(get3(c->a,0))||!rm_vec_finite(get3(c->b,0))||!rm_vec_finite(get3(c->c,0))||
            !isfinite(c->friction)||c->friction<0||c->friction>5||(c->type==0&&fabs(rm_norm(get3(c->a,0))-1)>1e-9)||
            (c->type==1&&c->b[0]<=0)||(c->type==2&&(c->b[0]<=0||c->b[1]<=0||c->b[2]<=0))||
            (c->type==3&&(c->c[0]<=0||rm_norm(rm_sub(get3(c->b,0),get3(c->a,0)))<=1e-12)))return 0;}
    for(int i=0;i<s->points;i++) {CoupledPoint *p=&s->point[i];if(!rm_vec_finite(p->position)||!rm_vec_finite(p->velocity)||!isfinite(p->inverse_mass+p->radius)||p->inverse_mass<0||p->radius<0||p->radius>10||(p->field_mask>>s->fields)||(p->inverse_mass==0&&rm_norm(p->velocity)>0))return 0;}
    for(int i=0;i<s->rigids;i++) {CoupledRigid *r=&s->rigid[i];if(r->shape<0||r->shape>2||!rm_vec_finite(r->body.position)||!rm_vec_finite(r->body.velocity)||!rm_vec_finite(r->body.angular_momentum)||!rm_q_valid(r->body.orientation)||fabs(rm_q_norm(r->body.orientation)-1)>1e-6||!rm_inertia_valid(r->body.inertia_body_diag)||!rm_vec_finite(r->size)||!rm_vec_finite(r->pivot_local)||!rm_vec_finite(r->pivot_point)||!(r->mass>0)||!isfinite(r->mass+r->radius+r->height+r->friction+r->restitution+r->body.inv_mass)||r->body.inv_mass<0||r->friction<0||r->friction>5||r->restitution<0||r->restitution>1||(r->shape==0&&r->radius<=0)||(r->shape==1&&(r->size.x<=0||r->size.y<=0||r->size.z<=0))||(r->shape==2&&(r->radius<=0||r->height<=0))||r->fixed<0||r->fixed>1||r->pivot<0||r->pivot>1||(r->fixed&&r->pivot))return 0;
        if((r->fixed||r->pivot)?r->body.inv_mass!=0:fabs(r->body.inv_mass*r->mass-1)>1e-9)return 0;
        if(r->fixed&&(rm_norm(r->body.velocity)>0||rm_norm(r->body.angular_momentum)>0))return 0;
        if(r->pivot) {rm_vec3 offset=rm_q_rotate(r->body.orientation,r->pivot_local);
            rm_vec3 v=rm_cross(rm_world_omega(r->body.orientation,r->body.angular_momentum,r->body.inertia_body_diag),rm_scale(offset,-1));
            if(rm_norm(rm_sub(rm_add(r->body.position,offset),r->pivot_point))>1e-7||rm_norm(rm_sub(v,r->body.velocity))>1e-7*fmax(1,rm_norm(v)))return 0;}}
    for(int i=0;i<s->links;i++) {CoupledLink *l=&s->link[i];if(!valid_endpoint(s,l->a)||!valid_endpoint(s,l->b)||l->type<0||l->type>2||!(l->rest>0)||l->stiffness<0||l->damping<0||l->radius<0||l->radius>1||!isfinite(l->rest+l->stiffness+l->damping+l->radius)||
        (l->a.kind==l->b.kind&&l->a.index==l->b.index)||(l->type==0&&l->stiffness<=0)||(l->type!=0&&(l->stiffness!=0||l->damping!=0)))return 0;}
    for(int i=0;i<s->entities;i++) {CoupledEntity *e=&s->entity[i];if(e->start<0||e->count<1||e->kind<0||e->kind>3)return 0;
        if((e->kind==0&&(!s->particles||e->start>s->particles->particle_count-e->count))||(e->kind==1&&(e->start>=s->points||e->count!=1))||(e->kind==2&&(!s->mesh||e->start>=s->mesh->objects||e->count!=s->mesh->object[e->start].count))||(e->kind==3&&(e->start>=s->rigids||e->count!=1)))return 0;}
    for(int i=0;i<s->metrics;i++) {CoupledMetric *m=&s->metric[i];if(m->type<0||m->type>14||m->axis0<0||m->axis0>2||m->axis1<0||m->axis1>2||!rm_vec_finite(m->origin))return 0;
        if(m->type>=7&&m->type<=10) {if(m->a<0||m->a>=s->links||(m->type>=9&&s->link[m->a].type!=0))return 0;}
        else {if(m->a<0||m->a>=s->entities||m->b<0||m->b>=s->entities)return 0;int kind=s->entity[m->a].kind;
            if((m->type>=11&&kind!=3)||(m->type>=4&&m->type<=6&&kind!=2)||(m->type==0&&kind!=0&&kind!=2))return 0;
            if(m->type==4&&(!s->mesh->object[s->entity[m->a].start].closed||fabs(s->mesh->object[s->entity[m->a].start].rest_volume)<1e-15))return 0;}}
    return 1;
}
uint32_t coupled_abi_version(void) {return COUPLED_ABI;}
int32_t coupled_simulate(CoupledSimulation *s,CoupledDiagnostics *d,PhyDiagnostics *pd,MeshDiagnostics *md) {
    if(!d||!pd||!md)return 1;memset(d,0,sizeof(*d));memset(pd,0,sizeof(*pd));memset(md,0,sizeof(*md));d->finite=1;
    if(!validate(s))return d->status=1;
    World w={0};w.s=s;w.d=d;w.pd=pd;w.md=md;w.started=now();w.feature=1;
    if(s->deadline_seconds<=0)return d->status=3;
    int np=s->particles?s->particles->particle_count:0,nm=s->mesh?s->mesh->vertices:0;
    w.old_particles=calloc((size_t)np+1,sizeof(rm_vec3));w.old_mesh=calloc((size_t)nm+1,sizeof(rm_vec3));w.initial_mesh=calloc((size_t)nm+1,sizeof(rm_vec3));w.old_points=calloc((size_t)s->points+1,sizeof(rm_vec3));w.old_rigids=calloc((size_t)s->rigids+1,sizeof(CoupledRigid));
    if(!w.old_particles||!w.old_mesh||!w.initial_mesh||!w.old_points||!w.old_rigids) {d->status=2;goto cleanup;}
    if(s->particles) {w.particle_inverse_mass=1/(1000*pow(s->particles->spacing,3));w.feature=fmin(w.feature,s->particles->spacing);w.particles=phy_context_create(s->particles,pd);if(!w.particles){d->status=pd->status;goto cleanup;}
        phy_context_set_velocity_decay(w.particles,0);}
    if(s->mesh) {d->status=mesh_context_create(s->mesh,&w.mesh,md);if(d->status)goto cleanup;for(int i=0;i<nm;i++)w.initial_mesh[i]=get3(s->mesh->positions,i);for(int i=0;i<s->mesh->edges;i++)w.feature=fmin(w.feature,s->mesh->edge[i].rest);
        for(int i=0;i<s->mesh->objects;i++)w.feature=fmin(w.feature,s->mesh->object[i].thickness);}
    if(!validate_resolution(&w)){d->status=1;goto cleanup;}
    for(int i=0;i<s->points;i++)if(s->point[i].radius>0)w.feature=fmin(w.feature,s->point[i].radius);
    for(int i=0;i<s->links;i++)if(s->link[i].radius>0)w.feature=fmin(w.feature,s->link[i].radius);
    for(int i=0;i<s->rigids;i++){CoupledRigid *r=&s->rigid[i];double feature=r->shape==1?.5*fmin(r->size.x,fmin(r->size.y,r->size.z)):r->shape==2?fmin(r->radius,.5*r->height):r->radius;w.feature=fmin(w.feature,feature);}
    snapshot(&w);frame(&w,0,1);observe(&w,0);d->initial_momentum=momentum(&w);
    double t=0;int next_frame=1;
    while(t<s->duration-1e-12) {
        double remaining=s->duration-t;if(remaining<=fmax(1e-12,s->dt*1e-5)){t=s->duration;break;}
        double dt=fmin(s->dt,remaining),end=t+dt,local=t;int used=0;
        while(local<end-1e-12) {
            if(now()-w.started>=s->deadline_seconds) {d->status=3;goto finished;}
            double maximum=speed(&w);if(!isfinite(maximum)){d->status=4;goto finished;}
            double h=fmin(dt/s->substeps,end-local),cfl=.2*w.feature/fmax(maximum+rm_norm(s->gravity)*dt,1e-6);h=fmin(h,cfl);
            for(int i=0;i<s->fields;i++) {double a=s->field[i].start_time,b=s->field[i].end_time;if(a>local+1e-12)h=fmin(h,a-local);if(b>local+1e-12)h=fmin(h,b-local);}
            if(!(h>0)||++used>64) {d->status=5;goto finished;}
            snapshot(&w);d->status=advance(&w,local,h);if(d->status)goto finished;
            local+=h;d->substeps++;d->simulated_time_s=local;d->max_speed_m_s=fmax(d->max_speed_m_s,speed(&w));
            if(!finite_state(&w)){d->status=4;goto finished;}
            while(next_frame<(int)ceil(s->duration*s->fps-1e-10)) {double ft=next_frame/s->fps;if(ft>local+1e-12)break;frame(&w,ft,clamp((ft-(local-h))/h,0,1));next_frame++;}
        }
        d->max_substeps_used=d->max_substeps_used>used?d->max_substeps_used:used;
        t=end;if(t>=s->duration-1e-12)t=s->duration;d->steps++;observe(&w,t);
    }
finished:
    d->completed=d->status==0&&t>=s->duration-1e-12;d->finite=d->status!=4;d->final_momentum=momentum(&w);
    if(d->completed){d->simulated_time_s=s->duration;frame(&w,s->duration,1);}
    if(w.particles) {int status=phy_context_refresh(w.particles);if(status&&!d->status)d->status=status;}
    if(w.mesh) {int status=mesh_context_diagnostics(w.mesh,md);if(status&&!d->status)d->status=status;
        d->max_penetration_m=fmax(d->max_penetration_m,md->max_contact_correction_m);}
cleanup:
    if(d->status)d->completed=0;
    if(d->status==4)d->finite=0;
    d->runtime_s=now()-w.started;phy_context_destroy(w.particles);mesh_context_destroy(w.mesh);
    free(w.old_particles);free(w.old_points);free(w.old_mesh);free(w.initial_mesh);free(w.old_rigids);return d->status;
}
