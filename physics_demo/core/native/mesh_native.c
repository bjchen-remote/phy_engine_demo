/* XPBD surface elasticity and bounded vertex/triangle contact.
 * Surface edges, opposite-vertex bending springs, and one signed-volume
 * constraint per closed body are an elastic shell proxy, not volumetric FEM.
 * No self collision, edge/edge CCD, cutting, or fracture is claimed. */
#define _POSIX_C_SOURCE 200809L
#include "mesh_native.h"
#include <float.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct { double x, y, z; } Vec;
typedef struct { Vec lo, hi; int start, count, left, right, owner; } Node;
typedef struct {
    MeshSimulation *s; MeshDiagnostics *d;
    double started, maximum_thickness, *previous, *initial, *lambda, *volume_lambda, *gradient;
    int *owner, *triangle_owner, *order, nodes, root;
    uint64_t candidates;
    Node *bvh;
} Work;

static Vec v(double x, double y, double z) { return (Vec){x,y,z}; }
static Vec add(Vec a, Vec b) { return v(a.x+b.x,a.y+b.y,a.z+b.z); }
static Vec sub(Vec a, Vec b) { return v(a.x-b.x,a.y-b.y,a.z-b.z); }
static Vec scale(Vec a, double s) { return v(a.x*s,a.y*s,a.z*s); }
static double dot(Vec a, Vec b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
static Vec cross(Vec a, Vec b) { return v(a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x); }
static double length(Vec a) { return sqrt(dot(a,a)); }
static Vec get(const double *p, int i) { return v(p[3*i],p[3*i+1],p[3*i+2]); }
static void put(double *p, int i, Vec a) { p[3*i]=a.x; p[3*i+1]=a.y; p[3*i+2]=a.z; }
static double component(Vec a, int k) { return k==0?a.x:(k==1?a.y:a.z); }
static double clamp(double x, double lo, double hi) { return fmin(hi,fmax(lo,x)); }
static Vec unit(Vec a, Vec fallback) { double n=length(a); return n>1e-15?scale(a,1/n):fallback; }
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (double)t.tv_sec+(double)t.tv_nsec*1e-9; }
static int expired(Work *w) { return now()-w->started >= w->s->deadline_seconds; }
static void extend(Node *n, Vec p) {
    n->lo=v(fmin(n->lo.x,p.x),fmin(n->lo.y,p.y),fmin(n->lo.z,p.z));
    n->hi=v(fmax(n->hi.x,p.x),fmax(n->hi.y,p.y),fmax(n->hi.z,p.z));
}
static void bound_triangle(Work *w, Node *n, int triangle) {
    for(int j=0;j<3;j++) { int i=w->s->indices[3*triangle+j];
        extend(n,get(w->s->positions,i)); extend(n,get(w->previous,i)); }
}
static void refit(Work *w, int i) {
    Node *n=&w->bvh[i]; n->lo=v(DBL_MAX,DBL_MAX,DBL_MAX); n->hi=scale(n->lo,-1);
    if(n->count) { for(int j=0;j<n->count;j++) bound_triangle(w,n,w->order[n->start+j]); }
    else { refit(w,n->left); refit(w,n->right);
        extend(n,w->bvh[n->left].lo); extend(n,w->bvh[n->left].hi);
        extend(n,w->bvh[n->right].lo); extend(n,w->bvh[n->right].hi); }
}
static double triangle_center(Work *w,int t,int axis) {
    double x=0; for(int j=0;j<3;j++) x+=w->s->positions[3*w->s->indices[3*t+j]+axis]; return x/3;
}
static int build(Work *w,int start,int count) {
    int i=w->nodes++; Node *n=&w->bvh[i]; n->start=start; n->count=count;
    n->owner=w->triangle_owner[w->order[start]];
    n->lo=v(DBL_MAX,DBL_MAX,DBL_MAX); n->hi=scale(n->lo,-1);
    for(int j=0;j<count;j++) {bound_triangle(w,n,w->order[start+j]);
        if(w->triangle_owner[w->order[start+j]]!=n->owner) n->owner=-1;}
    if(count<=8) return i;
    Vec extent=sub(n->hi,n->lo); int axis=extent.y>extent.x?1:0;
    if(extent.z>component(extent,axis)) axis=2;
    double middle=.5*(component(n->lo,axis)+component(n->hi,axis));
    int a=start,b=start+count-1;
    while(a<=b) { if(triangle_center(w,w->order[a],axis)<middle) a++;
        else { int t=w->order[a]; w->order[a]=w->order[b]; w->order[b--]=t; } }
    int left=a-start; if(left<count/4 || left>3*count/4) left=count/2;
    n->count=0; n->left=build(w,start,left); n->right=build(w,start+left,count-left); return i;
}
static int overlaps(Node *n,Vec a,Vec b,double radius) {
    return fmin(a.x,b.x)-radius<=n->hi.x && fmax(a.x,b.x)+radius>=n->lo.x
        && fmin(a.y,b.y)-radius<=n->hi.y && fmax(a.y,b.y)+radius>=n->lo.y
        && fmin(a.z,b.z)-radius<=n->hi.z && fmax(a.z,b.z)+radius>=n->lo.z;
}

/* Closest point with barycentric weights, including triangle edges and corners. */
static Vec closest(Vec p,Vec a,Vec b,Vec c,double weights[3]) {
    Vec ab=sub(b,a),ac=sub(c,a),ap=sub(p,a); double d1=dot(ab,ap),d2=dot(ac,ap);
    weights[0]=1; weights[1]=weights[2]=0;
    double edge_scale=fmax(dot(ab,ab),dot(ac,ac));
    if(dot(cross(ab,ac),cross(ab,ac))<=1e-24*edge_scale*edge_scale) {
        /* A deforming face may collapse to a segment; avoid 0/0 barycentrics. */
        Vec vertices[3]={a,b,c},best=a;double minimum=DBL_MAX;
        for(int i=0;i<3;i++) {int j=(i+1)%3;Vec edge=sub(vertices[j],vertices[i]);
            double t=clamp(dot(sub(p,vertices[i]),edge)/fmax(dot(edge,edge),1e-30),0,1);
            Vec q=add(vertices[i],scale(edge,t));double distance=dot(sub(p,q),sub(p,q));
            if(distance<minimum) {minimum=distance;best=q;weights[0]=weights[1]=weights[2]=0;weights[i]=1-t;weights[j]=t;}
        } return best;
    }
    if(d1<=0 && d2<=0) return a;
    Vec bp=sub(p,b); double d3=dot(ab,bp),d4=dot(ac,bp);
    if(d3>=0 && d4<=d3) { weights[0]=0; weights[1]=1; return b; }
    double vc=d1*d4-d3*d2;
    if(vc<=0 && d1>=0 && d3<=0) { double t=d1/(d1-d3); weights[0]=1-t;weights[1]=t;return add(a,scale(ab,t)); }
    Vec cp=sub(p,c); double d5=dot(ab,cp),d6=dot(ac,cp);
    if(d6>=0 && d5<=d6) { weights[0]=0;weights[2]=1;return c; }
    double vb=d5*d2-d1*d6;
    if(vb<=0 && d2>=0 && d6<=0) { double t=d2/(d2-d6);weights[0]=1-t;weights[2]=t;return add(a,scale(ac,t)); }
    double va=d3*d6-d5*d4;
    if(va<=0 && d4-d3>=0 && d5-d6>=0) { double t=(d4-d3)/(d4-d3+d5-d6);weights[0]=0;weights[1]=1-t;weights[2]=t;return add(b,scale(sub(c,b),t)); }
    double total=va+vb+vc;
    if(fabs(total)<1e-30) return a;
    weights[1]=vb/total;weights[2]=vc/total;weights[0]=1-weights[1]-weights[2];
    return add(a,add(scale(ab,weights[1]),scale(ac,weights[2])));
}
static double poly(double c[4],double t) { return ((c[3]*t+c[2])*t+c[1])*t+c[0]; }
/* A moving point and three linearly moving triangle vertices have a cubic
 * coplanarity equation. Isolate all roots at derivative extrema, then bisect. */
static int swept(Vec p0,Vec p1,Vec old[3],Vec current[3],double weights[3],Vec *normal) {
    Vec r0=sub(p0,old[0]),r1=sub(sub(p1,current[0]),r0);
    Vec a0=sub(old[1],old[0]),a1=sub(sub(current[1],current[0]),a0);
    Vec b0=sub(old[2],old[0]),b1=sub(sub(current[2],current[0]),b0);
    Vec n0=cross(a0,b0),n1=add(cross(a0,b1),cross(a1,b0)),n2=cross(a1,b1);
    double c[4]={dot(r0,n0),dot(r1,n0)+dot(r0,n1),dot(r1,n1)+dot(r0,n2),dot(r1,n2)};
    double cuts[4]={0,1,0,0}; int count=2;
    double qa=3*c[3],qb=2*c[2],qc=c[1],disc=qb*qb-4*qa*qc;
    if(fabs(qa)>1e-25 && disc>=0) { double root=sqrt(disc);
        double t=(-qb-root)/(2*qa); if(t>0 && t<1) cuts[count++]=t;
        t=(-qb+root)/(2*qa); if(t>0 && t<1) cuts[count++]=t;
    } else if(fabs(qb)>1e-25) { double t=-qc/qb;if(t>0 && t<1) cuts[count++]=t; }
    for(int j=1;j<count;j++) for(int k=j;k>0 && cuts[k]<cuts[k-1];k--) { double t=cuts[k];cuts[k]=cuts[k-1];cuts[k-1]=t; }
    double epsilon=1e-12*(fabs(c[0])+fabs(c[1])+fabs(c[2])+fabs(c[3])+1e-12);
    if(fabs(c[0])+fabs(c[1])+fabs(c[2])+fabs(c[3])<1e-25) return 0;
    for(int j=0;j<count-1;j++) {
        double lo=cuts[j],hi=cuts[j+1],fl=poly(c,lo),fh=poly(c,hi);
        if(fl*fh>0 && fabs(fl)>epsilon && fabs(fh)>epsilon) continue;
        if(fabs(fl)<=epsilon) hi=lo;
        else if(fabs(fh)>epsilon) for(int k=0;k<40;k++) { double mid=.5*(lo+hi),fm=poly(c,mid);
            if(fl*fm<=0) hi=mid;else {lo=mid;fl=fm;} }
        double t=hi; if(t<=1e-10) continue;
        Vec tri[3];for(int k=0;k<3;k++) tri[k]=add(old[k],scale(sub(current[k],old[k]),t));
        Vec p=add(p0,scale(sub(p1,p0),t));Vec q=closest(p,tri[0],tri[1],tri[2],weights);
        Vec n=cross(sub(tri[1],tri[0]),sub(tri[2],tri[0]));double nl=length(n);
        if(nl<1e-15 || length(sub(p,q))>1e-7) continue;
        n=scale(n,1/nl);double before=poly(c,fmax(0,t-1e-6));
        if(before<0) n=scale(n,-1);
        *normal=n;return 1;
    }
    return 0;
}
static void contact(Work *w,int vertex,int triangle,int measure) {
    MeshSimulation *s=w->s;int owner=w->owner[vertex],other=w->triangle_owner[triangle];
    if(owner==other || (s->object[owner].motion && s->object[other].motion)) return;
    int ids[3];Vec tri[3],old[3];double weights[3];
    for(int k=0;k<3;k++) {ids[k]=s->indices[3*triangle+k];tri[k]=get(s->positions,ids[k]);old[k]=get(w->previous,ids[k]);}
    Vec p=get(s->positions,vertex),q=closest(p,tri[0],tri[1],tri[2],weights),delta=sub(p,q);
    double radius=s->object[owner].thickness+s->object[other].thickness;
    double distance=length(delta),correction=radius-distance;Vec normal=unit(delta,unit(cross(sub(tri[1],tri[0]),sub(tri[2],tri[0])),v(0,1,0)));
    if(!measure) {
        Vec swept_normal;double swept_weights[3];
        if(swept(get(w->previous,vertex),p,old,tri,swept_weights,&swept_normal)) {
            Vec surface=v(0,0,0);for(int k=0;k<3;k++) surface=add(surface,scale(tri[k],swept_weights[k]));
            double swept_correction=radius-dot(sub(p,surface),swept_normal);
            if(swept_correction>correction) { correction=swept_correction;normal=swept_normal;memcpy(weights,swept_weights,sizeof(weights)); }
        }
    }
    if(correction<=1e-12) return;
    if(measure) {w->d->residual_penetration_m=fmax(w->d->residual_penetration_m,correction);return;}
    double denominator=s->inverse_mass[vertex];for(int k=0;k<3;k++) denominator+=weights[k]*weights[k]*s->inverse_mass[ids[k]];
    if(denominator<=0) return;
    Vec relative=sub(p,get(w->previous,vertex));
    for(int k=0;k<3;k++) relative=sub(relative,scale(sub(tri[k],old[k]),weights[k]));
    Vec tangent=sub(relative,scale(normal,dot(relative,normal)));double tl=length(tangent);
    double mu=sqrt(s->object[owner].friction*s->object[other].friction);
    Vec displacement=scale(normal,correction);
    if(tl>1e-15) displacement=sub(displacement,scale(tangent,fmin(1,mu*correction/tl)));
    put(s->positions,vertex,add(p,scale(displacement,s->inverse_mass[vertex]/denominator)));
    for(int k=0;k<3;k++) put(s->positions,ids[k],sub(get(s->positions,ids[k]),scale(displacement,weights[k]*s->inverse_mass[ids[k]]/denominator)));
    if(w->d->contact_count<INT32_MAX) w->d->contact_count++;
    w->d->max_contact_correction_m=fmax(w->d->max_contact_correction_m,correction);
}

static double segment_sphere(Vec p,Vec previous,Vec center,double radius,Vec *normal) {
    Vec r=sub(previous,center),d=sub(p,previous);double a=dot(d,d),b=dot(r,d),c=dot(r,r)-radius*radius;
    if(c>0 && a>1e-30 && b<0 && b*b-a*c>=0) {
        double t=(-b-sqrt(b*b-a*c))/a;
        if(t>=0 && t<=1) { *normal=unit(add(r,scale(d,t)),v(0,1,0));return fmax(0,radius-dot(sub(p,center),*normal)); }
    }
    Vec delta=sub(p,center);double dist=length(delta);*normal=unit(delta,v(0,1,0));return radius-dist;
}
static double analytic(Vec p,Vec previous,double radius,MeshCollider *c,Vec *normal,int measure) {
    Vec a=get(c->a,0),b=get(c->b,0);*normal=v(0,1,0);
    if(c->type==0) { *normal=a;return radius-dot(p,a)+c->b[0]; }
    if(c->type==1) return segment_sphere(p,measure?p:previous,a,radius+c->b[0],normal);
    if(c->type==3) {
        Vec ab=sub(b,a);double t=clamp(dot(sub(p,a),ab)/fmax(dot(ab,ab),1e-30),0,1);
        Vec q=add(a,scale(ab,t));Vec delta=sub(p,q);double dist=length(delta);*normal=unit(delta,v(1,0,0));
        double result=radius+c->c[0]-dist;
        if(!measure) { /* Swept capsule: cylinder intersection plus spherical caps. */
            double len=length(ab);Vec axis=unit(ab,v(0,1,0)),r=sub(previous,a),d=sub(p,previous);
            Vec rp=sub(r,scale(axis,dot(r,axis))),dp=sub(d,scale(axis,dot(d,axis)));
            double aa=dot(dp,dp),bb=dot(rp,dp),rr=radius+c->c[0],cc=dot(rp,rp)-rr*rr;
            if(aa>1e-30 && cc>0 && bb<0 && bb*bb-aa*cc>=0) {double hit=(-bb-sqrt(bb*bb-aa*cc))/aa;
                double h=dot(add(r,scale(d,hit)),axis);if(hit>=0 && hit<=1 && h>=0 && h<=len) {
                    Vec n=unit(add(rp,scale(dp,hit)),v(1,0,0));double correction=rr-dot(sub(p,a),n);
                    if(correction>result) {*normal=n;result=correction;}}}
            for(int k=0;k<2;k++) {Vec n;double correction=segment_sphere(p,previous,k?b:a,rr,&n);
                if(correction>result) {*normal=n;result=correction;}}
        } return result;
    }
    Vec local=sub(p,a),old=sub(previous,a),half=add(scale(b,.5),v(radius,radius,radius));
    if(!measure) {
        double enter=0,leave=1;int axis=-1;double sign=1;
        for(int k=0;k<3;k++) {double x=component(old,k),d=component(sub(local,old),k),h=component(half,k);
            if(fabs(d)<1e-20) {if(fabs(x)>=h) {leave=-1;break;}continue;}
            double near=(-h-x)/d,far=(h-x)/d;if(near>far) {double temp=near;near=far;far=temp;}
            if(near>=enter) {enter=near;axis=k;sign=d>0?-1:1;}leave=fmin(leave,far);
        }
        if(axis>=0 && enter<=leave && enter>=0 && enter<=1) {
            *normal=axis==0?v(sign,0,0):(axis==1?v(0,sign,0):v(0,0,sign));
            return component(half,axis)-sign*component(local,axis);
        }
    }
    if(fabs(local.x)>=half.x || fabs(local.y)>=half.y || fabs(local.z)>=half.z) return 0;
    int axis=0;double result=half.x-fabs(local.x);
    for(int k=1;k<3;k++) if(component(half,k)-fabs(component(local,k))<result) {axis=k;result=component(half,k)-fabs(component(local,k));}
    double sign=component(local,axis)>=0?1:-1;*normal=axis==0?v(sign,0,0):(axis==1?v(0,sign,0):v(0,0,sign));return result;
}
static void fixed_contact(Work *w,int i,Vec normal,double correction,double friction,int measure) {
    if(correction<=1e-12) return;
    if(measure) {w->d->residual_penetration_m=fmax(w->d->residual_penetration_m,correction);return;}
    Vec p=get(w->s->positions,i),displacement=sub(p,get(w->previous,i));
    Vec tangent=sub(displacement,scale(normal,dot(displacement,normal)));double tl=length(tangent);
    p=add(p,scale(normal,correction));if(tl>1e-15) p=sub(p,scale(tangent,fmin(1,friction*correction/tl)));
    put(w->s->positions,i,p);if(w->d->contact_count<INT32_MAX) w->d->contact_count++;
    w->d->max_contact_correction_m=fmax(w->d->max_contact_correction_m,correction);
}
static int collisions(Work *w,int measure) {
    MeshSimulation *s=w->s;if(s->objects>1) refit(w,w->root);
    for(int i=0;i<s->vertices;i++) {
        if((i&127)==0 && expired(w)) return 0;
        MeshObject *object=&s->object[w->owner[i]];double radius=object->thickness;
        if(s->inverse_mass[i]>0) {
            for(int j=0;j<s->colliders;j++) {Vec n;double c=analytic(get(s->positions,i),get(w->previous,i),radius,&s->collider[j],&n,measure);
                fixed_contact(w,i,n,c,sqrt(object->friction*s->collider[j].friction),measure);}
            for(int k=0;k<3;k++) {
                Vec n=k==0?v(1,0,0):(k==1?v(0,1,0):v(0,0,1));
                fixed_contact(w,i,n,s->bounds_min[k]+radius-s->positions[3*i+k],object->friction,measure);
                fixed_contact(w,i,scale(n,-1),s->positions[3*i+k]+radius-s->bounds_max[k],object->friction,measure);
            }
        }
        if(s->objects==1) continue;
        int stack[64],size=1;stack[0]=w->root;
        while(size) {Node *n=&w->bvh[stack[--size]];
            if(n->owner==w->owner[i]) continue;
            if(!overlaps(n,get(s->positions,i),get(w->previous,i),radius+w->maximum_thickness)) continue;
            if(n->count) {for(int j=0;j<n->count;j++) {
                if((++w->candidates&4095)==0 && expired(w)) return 0;
                contact(w,i,w->order[n->start+j],measure);}}
            else {if(size>61) return 0;stack[size++]=n->left;stack[size++]=n->right;}
        }
    } return 1;
}

static double volume(Work *w,MeshObject *object,int gradient) {
    MeshSimulation *s=w->s;Vec origin=get(s->positions,object->start);double result=0;
    if(gradient) memset(w->gradient+3*object->start,0,sizeof(double)*3*(size_t)object->count);
    for(int j=0;j<object->triangle_count;j++) {
        int *ids=&s->indices[3*(object->triangle_start+j)];Vec p[3];
        for(int k=0;k<3;k++) p[k]=sub(get(s->positions,ids[k]),origin);
        result+=dot(p[0],cross(p[1],p[2]))/6;
        if(gradient) for(int k=0;k<3;k++) put(w->gradient,ids[k],add(get(w->gradient,ids[k]),scale(cross(p[(k+1)%3],p[(k+2)%3]),1.0/6)));
    } return result;
}
static void constraints(Work *w,double h) {
    MeshSimulation *s=w->s;
    for(int i=0;i<s->edges;i++) {MeshEdge *e=&s->edge[i];Vec a=get(s->positions,e->a),b=get(s->positions,e->b),d=sub(a,b);double len=length(d);
        double alpha=e->compliance/(h*h),den=s->inverse_mass[e->a]+s->inverse_mass[e->b]+alpha;
        if(len<=1e-15 || den<=0) continue;
        double dl=(-(len-e->rest)-alpha*w->lambda[i])/den;w->lambda[i]+=dl;Vec change=scale(d,dl/len);
        put(s->positions,e->a,add(a,scale(change,s->inverse_mass[e->a])));
        put(s->positions,e->b,sub(b,scale(change,s->inverse_mass[e->b])));
    }
    for(int j=0;j<s->objects;j++) {MeshObject *o=&s->object[j];if(!o->closed || o->motion!=0) continue;
        double value=volume(w,o,1),alpha=o->compliance/(h*h),den=alpha;
        for(int i=o->start;i<o->start+o->count;i++) den+=s->inverse_mass[i]*dot(get(w->gradient,i),get(w->gradient,i));
        if(den<=1e-30) continue;
        double dl=(-(value-o->rest_volume)-alpha*w->volume_lambda[j])/den;w->volume_lambda[j]+=dl;
        for(int i=o->start;i<o->start+o->count;i++) put(s->positions,i,add(get(s->positions,i),scale(get(w->gradient,i),dl*s->inverse_mass[i])));
    }
}
static int diagnostics(Work *w) {
    MeshSimulation *s=w->s;
    for(int i=0;i<3*s->vertices;i++) if(!isfinite(s->positions[i]) || !isfinite(s->velocities[i]) || fabs(s->positions[i])>1e9) return 0;
    for(int i=0;i<s->edges;i++) {MeshEdge *e=&s->edge[i];if(e->bending) continue;
        w->d->max_edge_strain=fmax(w->d->max_edge_strain,fabs(length(sub(get(s->positions,e->a),get(s->positions,e->b)))/e->rest-1));}
    for(int i=0;i<s->objects;i++) {MeshObject *o=&s->object[i];if(o->motion || !o->closed) continue;
        double ratio=volume(w,o,0)/o->rest_volume;
        w->d->min_volume_ratio=fmin(w->d->min_volume_ratio,ratio);w->d->max_volume_ratio=fmax(w->d->max_volume_ratio,ratio);
        if(ratio<=0) w->d->inverted_objects++;
    } return 1;
}
static void observe(Work *w,double t) {
    MeshSimulation *s=w->s;if(!s->metrics) return;int row=w->d->observations_written++;
    s->observation_times[row]=t;Vec centers[16],velocities[16];
    for(int j=0;j<s->objects;j++) {MeshObject *o=&s->object[j];Vec center=v(0,0,0),velocity=v(0,0,0);
        for(int i=o->start;i<o->start+o->count;i++) {center=add(center,get(s->positions,i));velocity=add(velocity,get(s->velocities,i));}
        centers[j]=scale(center,1.0/o->count);velocities[j]=scale(velocity,1.0/o->count);}
    for(int j=0;j<s->metrics;j++) {MeshMetric *m=&s->metric[j];double value=0;
        if(m->type==0) {MeshObject *o=&s->object[m->a];for(int i=o->start;i<o->start+o->count;i++) {
            double a=s->positions[3*i+m->axis0]-m->origin[m->axis0],b=s->positions[3*i+m->axis1]-m->origin[m->axis1];value=fmax(value,a*a+b*b);}value=sqrt(value);}
        else if(m->type==1) value=component(centers[m->a],m->axis0);
        else if(m->type==2) value=length(velocities[m->a]);
        else if(m->type==3) value=length(sub(centers[m->a],centers[m->b]));
        else if(m->type==4) value=volume(w,&s->object[m->a],0)/s->object[m->a].rest_volume;
        else if(m->type==5) {MeshObject *o=&s->object[m->a];for(int i=o->start;i<o->start+o->count;i++) value=fmax(value,length(sub(get(s->positions,i),get(w->initial,i))));}
        else for(int i=0;i<s->edges;i++) {MeshEdge *e=&s->edge[i];if(!e->bending && w->owner[e->a]==m->a) value=fmax(value,fabs(length(sub(get(s->positions,e->a),get(s->positions,e->b)))/e->rest-1));}
        s->observation_values[row*s->metrics+j]=value;
    }
}
static void frame(Work *w,double t,double blend,const double *macro_start) {
    MeshSimulation *s=w->s;int row=w->d->frames_written++;s->frame_times[row]=t;
    for(int i=0;i<3*s->vertices;i++) s->frames[row*3*s->vertices+i]=(float)(macro_start[i]+blend*(s->positions[i]-macro_start[i]));
}
static int validate(MeshSimulation *s) {
    if(!s || s->abi!=MESH_ABI || s->vertices<3 || s->vertices>12000 || s->triangles<1 || s->triangles>24000
        || s->objects<1 || s->objects>16 || s->edges<0 || s->edges>144000 || s->colliders<0 || s->colliders>64
        || s->metrics<0 || s->metrics>16 || s->iterations<1 || s->iterations>32 || s->substeps<1 || s->substeps>32
        || s->frame_capacity<2 || s->frame_capacity>2401 || (double)s->frame_capacity*s->vertices>4000000
        || s->observation_capacity<0 || s->observation_capacity>250002
        || !(s->dt>0) || !(s->duration>0) || !(s->fps>0) || !isfinite(s->dt+s->duration+s->fps+s->deadline_seconds)
        || s->duration/s->dt>250000 || !isfinite(s->duration*s->fps) || s->duration*s->fps>2400
        || !s->positions || !s->velocities || !s->inverse_mass || !s->indices
        || !s->object || !s->edge || !s->collider || !s->metric || !s->frames || !s->frame_times) return 0;
    if(s->frame_capacity<(int)fmax(1,ceil(s->duration*s->fps-1e-10))+1 || (s->metrics && (!s->observation_times || !s->observation_values || s->observation_capacity<(int)ceil(s->duration/s->dt)+1))) return 0;
    for(int k=0;k<3;k++) if(!isfinite(s->gravity[k]) || !isfinite(s->bounds_min[k]) || !isfinite(s->bounds_max[k]) || s->bounds_min[k]>=s->bounds_max[k]) return 0;
    for(int i=0;i<s->vertices;i++) if(!isfinite(s->inverse_mass[i]) || s->inverse_mass[i]<0) return 0;
    for(int i=0;i<s->triangles*3;i++) if(s->indices[i]<0 || s->indices[i]>=s->vertices) return 0;
    int vertex_end=0,triangle_end=0;
    for(int i=0;i<s->objects;i++) {MeshObject *o=&s->object[i];
        if(o->start!=vertex_end || o->triangle_start!=triangle_end || o->count<3 || o->triangle_count<1
            || o->count>s->vertices-vertex_end || o->triangle_count>s->triangles-triangle_end || o->motion<0 || o->motion>2
            || !(o->thickness>0) || o->thickness>.5 || o->compliance<0 || o->damping<0 || o->friction<0 || o->friction>5
            || !isfinite(o->thickness+o->compliance+o->damping+o->friction+o->rest_volume)
            || (o->closed && fabs(o->rest_volume)<1e-15)) return 0;
        for(int k=0;k<3;k++) if(!isfinite(o->velocity[k])) return 0;
        vertex_end+=o->count;triangle_end+=o->triangle_count;
        for(int j=o->start;j<vertex_end;j++) if(o->motion && s->inverse_mass[j]!=0) return 0;
        for(int j=o->triangle_start;j<triangle_end;j++) for(int k=0;k<3;k++) if(s->indices[3*j+k]<o->start || s->indices[3*j+k]>=vertex_end) return 0;
    }
    if(vertex_end!=s->vertices || triangle_end!=s->triangles) return 0;
    for(int i=0;i<s->edges;i++) {MeshEdge *e=&s->edge[i];if(e->a<0 || e->b<0 || e->a>=s->vertices || e->b>=s->vertices || !(e->rest>1e-15) || e->compliance<0 || !isfinite(e->rest+e->compliance)) return 0;}
    for(int i=0;i<s->colliders;i++) {MeshCollider *c=&s->collider[i];if(c->type<0 || c->type>3 || !isfinite(c->friction) || c->friction<0 || c->friction>5) return 0;
        for(int k=0;k<3;k++) if(!isfinite(c->a[k]+c->b[k]+c->c[k])) return 0;
        if((c->type==0 && fabs(length(get(c->a,0))-1)>1e-8) || (c->type==1 && c->b[0]<=0) || (c->type==2 && (c->b[0]<=0 || c->b[1]<=0 || c->b[2]<=0)) || (c->type==3 && c->c[0]<=0)) return 0;
    }
    for(int i=0;i<s->metrics;i++) {MeshMetric *m=&s->metric[i];if(m->type<0 || m->type>6 || m->a<0 || m->a>=s->objects || m->b<0 || m->b>=s->objects || m->axis0<0 || m->axis0>2 || m->axis1<0 || m->axis1>2 || (m->type==4 && !s->object[m->a].closed)) return 0;
        for(int k=0;k<3;k++) if(!isfinite(m->origin[k])) return 0;}
    return 1;
}
struct MeshContext {
    MeshSimulation simulation;
    MeshDiagnostics diagnostic;
    Work work;
    double *projection_start;
    double min_edge;
};

static void initialize_diagnostics(MeshDiagnostics *d) {
    memset(d,0,sizeof(*d));d->finite=1;d->min_volume_ratio=d->max_volume_ratio=1;
}
static int context_result(MeshContext *context,MeshDiagnostics *output,int status) {
    if(status) context->diagnostic.status=status;
    context->diagnostic.runtime_s=now()-context->work.started;
    if(output) *output=context->diagnostic;
    return context->diagnostic.status;
}
void mesh_context_destroy(MeshContext *context) {
    if(!context) return;
    Work *w=&context->work;
    free(w->previous);free(w->initial);free(w->gradient);free(w->lambda);free(w->volume_lambda);
    free(w->owner);free(w->triangle_owner);free(w->order);free(w->bvh);
    free(context->projection_start);free(context);
}
int32_t mesh_context_create(const MeshSimulation *simulation,MeshContext **output,MeshDiagnostics *d) {
    MeshDiagnostics diagnostic;
    initialize_diagnostics(&diagnostic);
    if(output) *output=NULL;
    if(!simulation || !output) {diagnostic.status=1;if(d) *d=diagnostic;return 1;}
    MeshSimulation parameters=*simulation;
    if(!validate(&parameters)) {diagnostic.status=1;if(d) *d=diagnostic;return 1;}
    if(parameters.deadline_seconds<=0) {diagnostic.status=3;if(d) *d=diagnostic;return 3;}
    MeshContext *context=calloc(1,sizeof(*context));
    if(!context) {diagnostic.status=2;if(d) *d=diagnostic;return 2;}
    context->simulation=parameters;context->diagnostic=diagnostic;
    Work *w=&context->work;MeshSimulation *s=&context->simulation;
    w->s=s;w->d=&context->diagnostic;w->started=now();
    size_t vectors=(size_t)s->vertices*3*sizeof(double);
    w->previous=malloc(vectors);w->initial=malloc(vectors);w->gradient=malloc(vectors);
    context->projection_start=malloc(vectors);
    w->lambda=calloc((size_t)s->edges+1,sizeof(double));w->volume_lambda=calloc((size_t)s->objects,sizeof(double));
    w->owner=malloc((size_t)s->vertices*sizeof(int));w->triangle_owner=malloc((size_t)s->triangles*sizeof(int));
    w->order=malloc((size_t)s->triangles*sizeof(int));w->bvh=calloc((size_t)s->triangles*2,sizeof(Node));
    if(!w->previous || !w->initial || !w->gradient || !context->projection_start || !w->lambda
       || !w->volume_lambda || !w->owner || !w->triangle_owner || !w->order || !w->bvh) {
        int status=context_result(context,d,2);mesh_context_destroy(context);return status;
    }
    memcpy(w->previous,s->positions,vectors);memcpy(w->initial,s->positions,vectors);
    for(int j=0;j<s->objects;j++) {MeshObject *o=&s->object[j];
        if(o->closed && !o->motion) w->d->closed_objects++;
        w->maximum_thickness=fmax(w->maximum_thickness,o->thickness);
        for(int i=o->start;i<o->start+o->count;i++) w->owner[i]=j;
        for(int i=o->triangle_start;i<o->triangle_start+o->triangle_count;i++) w->triangle_owner[i]=j;
    }
    if(!diagnostics(w)) {
        w->d->finite=0;int status=context_result(context,d,4);mesh_context_destroy(context);return status;
    }
    for(int j=0;j<s->triangles;j++) w->order[j]=j;
    w->root=build(w,0,s->triangles);context->min_edge=1;
    for(int i=0;i<s->edges;i++) context->min_edge=fmin(context->min_edge,s->edge[i].rest);
    *output=context;return context_result(context,d,0);
}
int32_t mesh_context_refit(MeshContext *context) {
    if(!context) return 1;
    if(context->diagnostic.status) return context->diagnostic.status;
    if(expired(&context->work)) return context_result(context,NULL,3);
    MeshSimulation *s=&context->simulation;
    for(int i=0;i<3*s->vertices;i++) if(!isfinite(s->positions[i])) {
        context->diagnostic.finite=0;return context_result(context,NULL,4);
    }
    refit(&context->work,context->work.root);return 0;
}
static void update_speed(Work *w) {
    for(int i=0;i<w->s->vertices;i++)
        w->d->maximum_speed_m_s=fmax(w->d->maximum_speed_m_s,length(get(w->s->velocities,i)));
}
int32_t mesh_context_step(MeshContext *context,double h,MeshDiagnostics *output) {
    if(!context || !isfinite(h) || h<=0) return 1;
    Work *w=&context->work;MeshSimulation *s=w->s;
    if(w->d->status) return context_result(context,output,0);
    if(expired(w)) return context_result(context,output,3);
    if(!diagnostics(w)) {w->d->finite=0;return context_result(context,output,4);}
    size_t vectors=(size_t)s->vertices*3*sizeof(double);
    memcpy(w->previous,s->positions,vectors);
    memset(w->lambda,0,((size_t)s->edges+1)*sizeof(double));
    memset(w->volume_lambda,0,(size_t)s->objects*sizeof(double));
    for(int i=0;i<s->vertices;i++) {MeshObject *o=&s->object[w->owner[i]];Vec velocity=get(s->velocities,i);
        if(s->inverse_mass[i]>0) {
            velocity=scale(add(velocity,scale(get(s->gravity,0),h)),exp(-o->damping*h));
            put(s->velocities,i,velocity);put(s->positions,i,add(get(s->positions,i),scale(velocity,h)));
        } else if(o->motion==2) put(s->positions,i,add(get(s->positions,i),scale(get(o->velocity,0),h)));
    }
    for(int iteration=0;iteration<s->iterations;iteration++) {
        constraints(w,h);if(!collisions(w,0)) return context_result(context,output,3);
    }
    for(int i=0;i<s->vertices;i++) put(s->velocities,i,scale(sub(get(s->positions,i),get(w->previous,i)),1/h));
    update_speed(w);w->d->substeps++;w->d->simulated_time_s+=h;
    if(!diagnostics(w)) {w->d->finite=0;return context_result(context,output,4);}
    refit(w,w->root);
    return context_result(context,output,0);
}
int32_t mesh_context_project(MeshContext *context,double h,MeshDiagnostics *output) {
    if(!context || !isfinite(h) || h<=0) return 1;
    Work *w=&context->work;MeshSimulation *s=w->s;
    if(w->d->status) return context_result(context,output,0);
    if(expired(w)) return context_result(context,output,3);
    if(!diagnostics(w)) {w->d->finite=0;return context_result(context,output,4);}
    memcpy(context->projection_start,s->positions,(size_t)s->vertices*3*sizeof(double));
    constraints(w,h);
    if(!collisions(w,0)) return context_result(context,output,3);
    for(int i=0;i<s->vertices;i++) {
        Vec correction=scale(sub(get(s->positions,i),get(context->projection_start,i)),1/h);
        put(s->velocities,i,add(get(s->velocities,i),correction));
    }
    update_speed(w);
    if(!diagnostics(w)) {w->d->finite=0;return context_result(context,output,4);}
    refit(w,w->root);
    return context_result(context,output,0);
}
int32_t mesh_context_diagnostics(MeshContext *context,MeshDiagnostics *output) {
    if(!context || !output) return 1;
    if(!diagnostics(&context->work)) {context->diagnostic.finite=0;return context_result(context,output,4);}
    update_speed(&context->work);
    return context_result(context,output,0);
}
/* External point contact uses the same current-geometry closest-point and
 * BVH kernels as mesh/mesh contact. A normal velocity impulse is distributed
 * with barycentric masses; friction is bounded by its Coulomb normal impulse.
 * Geometry projection uses the same equal/opposite mass-weighted response. */
int32_t mesh_context_contact_point(MeshContext *context,double position[3],double velocity[3],
    double inverse_mass,double radius,double h,double friction,int32_t *contact_count) {
    if(contact_count) *contact_count=0;
    if(!context || !position || !velocity || !contact_count || !isfinite(inverse_mass)
       || inverse_mass<0 || !isfinite(radius) || radius<0 || !isfinite(h) || h<=0
       || !isfinite(friction) || friction<0 || friction>5) return 1;
    for(int k=0;k<3;k++) if(!isfinite(position[k]) || !isfinite(velocity[k])) return 1;
    Work *w=&context->work;MeshSimulation *s=w->s;
    if(w->d->status) return w->d->status;
    if(expired(w)) return context_result(context,NULL,3);
    int stack[64],size=1;stack[0]=w->root;
    while(size) {
        Node *node=&w->bvh[stack[--size]];
        Vec point=get(position,0);
        if(!overlaps(node,point,point,radius+w->maximum_thickness)) continue;
        if(!node->count) {
            if(size>61) return context_result(context,NULL,4);
            stack[size++]=node->left;stack[size++]=node->right;continue;
        }
        for(int j=0;j<node->count;j++) {
            if((++w->candidates&4095)==0 && expired(w)) return context_result(context,NULL,3);
            int triangle=w->order[node->start+j],ids[3];Vec tri[3];double weights[3];
            MeshObject *object=&s->object[w->triangle_owner[triangle]];
            for(int k=0;k<3;k++) {
                ids[k]=s->indices[3*triangle+k];tri[k]=get(s->positions,ids[k]);
                for(int axis=0;axis<3;axis++) if(!isfinite(s->positions[3*ids[k]+axis])
                    || !isfinite(s->velocities[3*ids[k]+axis])) {
                    w->d->finite=0;return context_result(context,NULL,4);
                }
            }
            point=get(position,0);
            Vec surface=closest(point,tri[0],tri[1],tri[2],weights),delta=sub(point,surface);
            double distance=length(delta),correction=radius+object->thickness-distance;
            if(correction < -1e-10) continue;
            double denominator=inverse_mass;
            Vec relative=get(velocity,0);
            for(int k=0;k<3;k++) {
                denominator+=weights[k]*weights[k]*s->inverse_mass[ids[k]];
                relative=sub(relative,scale(get(s->velocities,ids[k]),weights[k]));
            }
            if(denominator<=0) continue;
            Vec normal=unit(delta,unit(cross(sub(tri[1],tri[0]),sub(tri[2],tri[0])),v(0,1,0)));
            if(distance<1e-15 && dot(relative,normal)>0) normal=scale(normal,-1);
            double normal_speed=dot(relative,normal);
            if(correction<=1e-12 && normal_speed>=0) continue;
            correction=fmax(0,correction);
            Vec displacement=scale(normal,correction/denominator);
            put(position,0,add(point,scale(displacement,inverse_mass)));
            for(int k=0;k<3;k++) put(s->positions,ids[k],sub(get(s->positions,ids[k]),
                scale(displacement,weights[k]*s->inverse_mass[ids[k]])));
            double normal_impulse=fmax(0,-normal_speed)/denominator;
            Vec impulse=scale(normal,normal_impulse),tangent=sub(relative,scale(normal,normal_speed));
            double tangent_speed=length(tangent),mu=sqrt(friction*object->friction);
            if(tangent_speed>1e-15) impulse=sub(impulse,scale(tangent,
                fmin(tangent_speed/denominator,mu*normal_impulse)/tangent_speed));
            put(velocity,0,add(get(velocity,0),scale(impulse,inverse_mass)));
            for(int k=0;k<3;k++) put(s->velocities,ids[k],sub(get(s->velocities,ids[k]),
                scale(impulse,weights[k]*s->inverse_mass[ids[k]])));
            for(int k=0;k<3;k++) for(int axis=0;axis<3;axis++)
                if(!isfinite(s->positions[3*ids[k]+axis]) || !isfinite(s->velocities[3*ids[k]+axis])) {
                    w->d->finite=0;return context_result(context,NULL,4);
                }
            if(*contact_count<INT32_MAX) (*contact_count)++;
            if(w->d->contact_count<INT32_MAX) w->d->contact_count++;
            w->d->max_contact_correction_m=fmax(w->d->max_contact_correction_m,correction);
            Vec corrected_surface=v(0,0,0);
            for(int k=0;k<3;k++) corrected_surface=add(corrected_surface,scale(get(s->positions,ids[k]),weights[k]));
            w->d->residual_penetration_m=fmax(w->d->residual_penetration_m,
                fmax(0,radius+object->thickness-length(sub(get(position,0),corrected_surface))));
        }
    }
    for(int k=0;k<3;k++) if(!isfinite(position[k]) || !isfinite(velocity[k])) {
        w->d->finite=0;return context_result(context,NULL,4);
    }
    return 0;
}
uint32_t mesh_abi_version(void) {return MESH_ABI;}
int32_t mesh_simulate(MeshSimulation *s,MeshDiagnostics *d) {
    if(!d) return 1;
    MeshContext *context=NULL;
    int status=mesh_context_create(s,&context,d);
    if(status) return status;
    Work *w=&context->work;
    /* The persistent and one-shot paths share allocation, integration,
     * constraints, diagnostics, and cleanup. Only scheduling/output differ. */
    frame(w,0,1,s->positions);observe(w,0);
    double t=0;int next_frame=1;
    while(t<s->duration-1e-12) {
        if(s->duration-t<=fmax(1e-12,s->dt*1e-5)) {
            t=s->duration;w->d->simulated_time_s=t;break;
        }
        if(expired(w)) {w->d->status=3;break;}
        double dt=fmin(s->dt,s->duration-t);
        double max_speed=0;for(int i=0;i<s->vertices;i++) max_speed=fmax(max_speed,length(get(s->velocities,i)));
        double requested=ceil(dt*(max_speed+length(get(s->gravity,0))*dt)/fmax(.5*context->min_edge,1e-8));
        int substeps=(int)fmax(s->substeps,fmin(32,requested));double h=dt/substeps;
        w->d->max_substeps_used=w->d->max_substeps_used>substeps?w->d->max_substeps_used:substeps;
        int finished=1;
        for(int step=0;step<substeps;step++) {
            if(mesh_context_step(context,h,NULL)) {finished=0;break;}
            w->d->simulated_time_s=t+(step+1)*h;
            while(next_frame<(int)ceil(s->duration*s->fps-1e-10)) {
                double ft=next_frame/s->fps;if(ft>w->d->simulated_time_s+1e-12) break;
                frame(w,ft,clamp((ft-(t+step*h))/h,0,1),w->previous);next_frame++;
            }
        }
        if(!finished) break;
        double end=t+dt;if(end>=s->duration-1e-12) end=s->duration;
        if(!collisions(w,1)) {w->d->status=3;break;}
        t=end;w->d->steps++;observe(w,t);
    }
    w->d->completed=w->d->status==0 && t>=s->duration-1e-12;
    if(w->d->completed) frame(w,s->duration,1,s->positions);
    status=context_result(context,d,0);mesh_context_destroy(context);return status;
}
