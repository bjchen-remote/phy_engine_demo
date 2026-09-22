#ifndef PHYSICS_DEMO_RIGID_MATH_H
#define PHYSICS_DEMO_RIGID_MATH_H

/* Standalone C11 rigid-body mathematics. Units: metres, seconds, kilograms.
 * Quaternions are (w,x,y,z) and rotate BODY vectors into WORLD coordinates.
 * Angular momentum and applied torques/impulses are in world coordinates.
 * inertia_body_diag contains positive PRINCIPAL moments about the integration
 * origin: normally the COM, or the anchor for a body with a fixed pivot.
 * All helpers have internal linkage; no ABI or external library is required.
 */
#include <float.h>
#include <math.h>
#include <stddef.h>

typedef struct { double x, y, z; } rm_vec3;
typedef struct { double w, x, y, z; } rm_quat;
typedef struct {
    rm_vec3 position, velocity;
    rm_quat orientation;
    rm_vec3 angular_momentum;
    double inv_mass;
    rm_vec3 inertia_body_diag;
} rm_body;

static inline rm_vec3 rm_v3(double x, double y, double z) {
    rm_vec3 v = {x, y, z}; return v;
}
static inline rm_vec3 rm_add(rm_vec3 a, rm_vec3 b) {
    return rm_v3(a.x+b.x, a.y+b.y, a.z+b.z);
}
static inline rm_vec3 rm_sub(rm_vec3 a, rm_vec3 b) {
    return rm_v3(a.x-b.x, a.y-b.y, a.z-b.z);
}
static inline rm_vec3 rm_scale(rm_vec3 v, double s) {
    return rm_v3(v.x*s, v.y*s, v.z*s);
}
static inline double rm_dot(rm_vec3 a, rm_vec3 b) {
    return a.x*b.x+a.y*b.y+a.z*b.z;
}
static inline rm_vec3 rm_cross(rm_vec3 a, rm_vec3 b) {
    return rm_v3(a.y*b.z-a.z*b.y, a.z*b.x-a.x*b.z, a.x*b.y-a.y*b.x);
}
static inline double rm_norm(rm_vec3 v) { return hypot(hypot(v.x,v.y),v.z); }
static inline int rm_vec_finite(rm_vec3 v) {
    return isfinite(v.x) && isfinite(v.y) && isfinite(v.z);
}
static inline int rm_inertia_valid(rm_vec3 i) {
    return rm_vec_finite(i) && i.x>0.0 && i.y>0.0 && i.z>0.0;
}
static inline rm_quat rm_q_identity(void) {
    rm_quat q = {1.0,0.0,0.0,0.0}; return q;
}
static inline double rm_q_norm(rm_quat q) {
    return hypot(hypot(q.w,q.x),hypot(q.y,q.z));
}
static inline int rm_q_valid(rm_quat q) {
    double n=rm_q_norm(q); return isfinite(n) && n>DBL_MIN;
}
/* Low-level normalization expects a finite, nonzero quaternion. Public step
 * routines check that precondition before changing any caller-owned state. */
static inline rm_quat rm_q_normalize(rm_quat q) {
    double n=rm_q_norm(q);
    q.w/=n; q.x/=n; q.y/=n; q.z/=n; return q;
}
static inline rm_quat rm_q_conjugate(rm_quat q) {
    rm_quat result={q.w,-q.x,-q.y,-q.z}; return result;
}
static inline rm_quat rm_q_mul(rm_quat a, rm_quat b) {
    rm_quat q={
        a.w*b.w-a.x*b.x-a.y*b.y-a.z*b.z,
        a.w*b.x+a.x*b.w+a.y*b.z-a.z*b.y,
        a.w*b.y-a.x*b.z+a.y*b.w+a.z*b.x,
        a.w*b.z+a.x*b.y-a.y*b.x+a.z*b.w
    }; return q;
}
/* Rotation helpers expect a unit quaternion. */
static inline rm_vec3 rm_q_rotate(rm_quat q, rm_vec3 v) {
    rm_vec3 u=rm_v3(q.x,q.y,q.z);
    rm_vec3 t=rm_scale(rm_cross(u,v),2.0);
    return rm_add(v,rm_add(rm_scale(t,q.w),rm_cross(u,t)));
}
static inline rm_vec3 rm_q_inverse_rotate(rm_quat q, rm_vec3 v) {
    return rm_q_rotate(rm_q_conjugate(q),v);
}
static inline rm_quat rm_q_from_rotation_vector(rm_vec3 angle) {
    double theta=rm_norm(angle), half=0.5*theta;
    double s=theta<1.0e-8 ? 0.5-theta*theta/48.0 : sin(half)/theta;
    rm_quat q={cos(half),s*angle.x,s*angle.y,s*angle.z}; return q;
}
static inline rm_quat rm_q_from_axis_angle(rm_vec3 axis, double angle) {
    double n=rm_norm(axis);
    return n>0.0 ? rm_q_from_rotation_vector(rm_scale(axis,angle/n)) : rm_q_identity();
}
static inline rm_vec3 rm_body_inverse_inertia(rm_vec3 inertia, rm_vec3 v) {
    return rm_v3(v.x/inertia.x,v.y/inertia.y,v.z/inertia.z);
}
static inline rm_vec3 rm_world_inverse_inertia(rm_quat q, rm_vec3 inertia, rm_vec3 v) {
    return rm_q_rotate(q,rm_body_inverse_inertia(inertia,rm_q_inverse_rotate(q,v)));
}
static inline rm_vec3 rm_world_omega(rm_quat q, rm_vec3 momentum, rm_vec3 inertia) {
    return rm_world_inverse_inertia(q,inertia,momentum);
}
static inline double rm_rotational_energy(rm_quat q, rm_vec3 momentum, rm_vec3 inertia) {
    return 0.5*rm_dot(momentum,rm_world_omega(q,momentum,inertia));
}

/* One second-order energy/momentum preserving free-rotation substep.
 * Solve m_mid=m0+(h/2)*(m_mid x I^-1*m_mid), then compose a Cayley
 * rotation q_delta=normalize(1,(h/2)*I^-1*m_mid). The corresponding m1
 * satisfies implicit midpoint Euler equations; |m| and m.I^-1.m are
 * quadratic invariants. World L is never reconstructed from omega.
 */
static inline int rm_rotation_midpoint(rm_quat *q, rm_vec3 momentum,
                                       rm_vec3 inertia, double h) {
    rm_vec3 m0=rm_q_inverse_rotate(*q,momentum), mid=m0;
    double tolerance=32.0*DBL_EPSILON*fmax(rm_norm(m0),DBL_MIN);
    int iteration;
    for(iteration=0;iteration<80;++iteration) {
        rm_vec3 omega=rm_body_inverse_inertia(inertia,mid);
        rm_vec3 next=rm_add(m0,rm_scale(rm_cross(mid,omega),0.5*h));
        if(!rm_vec_finite(next)) return 0;
        if(rm_norm(rm_sub(next,mid))<=tolerance) {
            rm_vec3 a=rm_scale(rm_body_inverse_inertia(inertia,next),0.5*h);
            rm_quat delta={1.0,a.x,a.y,a.z};
            *q=rm_q_normalize(rm_q_mul(*q,rm_q_normalize(delta)));
            return rm_q_valid(*q);
        }
        mid=next;
    }
    return 0;
}

/* Fixed world angular momentum. Adaptive angular substeps improve the
 * nonlinear solve, without clamping angular velocity or changing energy.
 * Returns 0 for invalid/unrepresentable input or failure to converge; on
 * failure the input orientation is unchanged. Negative dt is supported.
 */
static inline int rm_step_orientation(rm_quat *orientation, rm_vec3 momentum,
                                      rm_vec3 inertia, double dt) {
    rm_quat q;
    double angle, count, h, minimum;
    size_t steps, k;
    if(orientation==NULL || !rm_q_valid(*orientation) || !rm_vec_finite(momentum)
       || !rm_inertia_valid(inertia) || !isfinite(dt)) return 0;
    q=rm_q_normalize(*orientation);
    minimum=fmin(inertia.x,fmin(inertia.y,inertia.z));
    angle=fabs(dt)*rm_norm(momentum)/minimum;
    if(!isfinite(angle)) return 0;
    /* Equal principal moments allow an exact exponential update. */
    if(inertia.x==inertia.y && inertia.y==inertia.z) {
        rm_quat delta=rm_q_from_rotation_vector(rm_scale(momentum,dt/inertia.x));
        q=rm_q_normalize(rm_q_mul(delta,q));
        if(!rm_q_valid(q)) return 0;
        *orientation=q; return 1;
    }
    count=fmax(1.0,ceil(angle/0.25));
    /* A caller-supplied step needing this much work must be split by its
     * budget-aware scheduler. This rejects work; it does not clamp state. */
    if(count>65536.0) return 0;
    steps=(size_t)count; h=dt/(double)steps;
    for(k=0;k<steps;++k) {
        if(!rm_rotation_midpoint(&q,momentum,inertia,h)) return 0;
    }
    *orientation=q; return 1;
}

/* Constant WORLD torque over this step: half kick, free drift, half kick.
 * The caller may instead evaluate orientation-dependent forces between
 * calls to rm_step_orientation to implement its own kick-drift-kick loop. */
static inline int rm_step_rotation(rm_quat *orientation, rm_vec3 *momentum,
                                   rm_vec3 inertia, rm_vec3 torque, double dt) {
    rm_vec3 half, result;
    rm_quat q;
    if(orientation==NULL || momentum==NULL || !rm_vec_finite(*momentum)
       || !rm_vec_finite(torque) || !isfinite(dt)) return 0;
    half=rm_add(*momentum,rm_scale(torque,0.5*dt));
    result=rm_add(*momentum,rm_scale(torque,dt));
    if(!rm_vec_finite(half) || !rm_vec_finite(result)) return 0;
    q=*orientation;
    if(!rm_step_orientation(&q,half,inertia,dt)) return 0;
    *orientation=q; *momentum=result; return 1;
}

/* The lever arm r runs from the integration origin to the WORLD contact.
 * For a free body the origin is its COM. For a fixed pivot, use r from the
 * anchor, inv_mass=0, and inertia about that anchor. n need not be unit. */
static inline double rm_contact_inv_mass(const rm_body *body, rm_vec3 r, rm_vec3 n) {
    rm_vec3 rxn=rm_cross(r,n);
    return body->inv_mass*rm_dot(n,n)+rm_dot(rxn,rm_world_inverse_inertia(
        body->orientation,body->inertia_body_diag,rxn));
}
static inline rm_vec3 rm_point_velocity(const rm_body *body, rm_vec3 r) {
    return rm_add(body->velocity,rm_cross(rm_world_omega(body->orientation,
        body->angular_momentum,body->inertia_body_diag),r));
}
static inline void rm_apply_impulse(rm_body *body, rm_vec3 r, rm_vec3 impulse) {
    body->velocity=rm_add(body->velocity,rm_scale(impulse,body->inv_mass));
    body->angular_momentum=rm_add(body->angular_momentum,rm_cross(r,impulse));
}
/* Geometric constraint correction. Jpos has units kg*m. The rotation is
 * the exponential of its first-order world angular displacement. This
 * changes position/orientation only; a solver that feeds corrections into
 * velocity applies Jpos/dt using rm_apply_impulse separately. Iterate and
 * recompute r/effective mass for large corrections. With inv_mass=0 a
 * pivot caller must then reconstruct COM position from anchor and q. */
static inline int rm_apply_position_impulse(rm_body *body, rm_vec3 r, rm_vec3 impulse) {
    rm_body result;
    rm_vec3 angle;
    if(body==NULL || !rm_q_valid(body->orientation)
       || !rm_inertia_valid(body->inertia_body_diag) || !rm_vec_finite(r)
       || !rm_vec_finite(impulse) || !rm_vec_finite(body->position)
       || !isfinite(body->inv_mass) || body->inv_mass<0.0) return 0;
    result=*body;
    result.orientation=rm_q_normalize(result.orientation);
    result.position=rm_add(result.position,rm_scale(impulse,result.inv_mass));
    angle=rm_world_inverse_inertia(result.orientation,result.inertia_body_diag,
        rm_cross(r,impulse));
    if(!rm_vec_finite(result.position) || !rm_vec_finite(angle)
       || !isfinite(rm_norm(angle))) return 0;
    result.orientation=rm_q_normalize(rm_q_mul(rm_q_from_rotation_vector(angle),
        result.orientation));
    if(!rm_q_valid(result.orientation)) return 0;
    *body=result; return 1;
}
static inline rm_vec3 rm_pivot_com(rm_quat q, rm_vec3 anchor, rm_vec3 com_offset_body) {
    return rm_add(anchor,rm_q_rotate(q,com_offset_body));
}
static inline rm_vec3 rm_pivot_gravity_torque(rm_quat q, rm_vec3 com_offset_body,
                                            rm_vec3 gravity, double mass) {
    return rm_cross(rm_q_rotate(q,com_offset_body),rm_scale(gravity,mass));
}
/* Fixed-point heavy top. body.inertia_body_diag is about the pivot, with
 * com_offset_body expressed in those same principal axes. In general an
 * arbitrary COM offset makes the pivot tensor non-diagonal: diagonalize
 * that tensor first and express q/offset in its principal frame.
 * The helper updates position and velocity to the physical COM, although
 * angular_momentum is about the anchor. Use anchor-based contact r and
 * omega x r directly for pivot contact velocities.
 */
static inline int rm_step_pivot(rm_body *body, rm_vec3 anchor, rm_vec3 com_offset_body,
                                rm_vec3 gravity, double mass, double dt) {
    rm_body result;
    rm_vec3 torque, half, offset, omega;
    if(body==NULL || !rm_vec_finite(anchor) || !rm_vec_finite(com_offset_body)
       || !rm_vec_finite(gravity) || !isfinite(mass) || mass<=0.0 || !isfinite(dt)) return 0;
    result=*body;
    if(!rm_q_valid(result.orientation) || !rm_vec_finite(result.angular_momentum)) return 0;
    result.orientation=rm_q_normalize(result.orientation);
    torque=rm_pivot_gravity_torque(result.orientation,com_offset_body,gravity,mass);
    half=rm_add(result.angular_momentum,rm_scale(torque,0.5*dt));
    if(!rm_step_orientation(&result.orientation,half,result.inertia_body_diag,dt)) return 0;
    torque=rm_pivot_gravity_torque(result.orientation,com_offset_body,gravity,mass);
    result.angular_momentum=rm_add(half,rm_scale(torque,0.5*dt));
    offset=rm_q_rotate(result.orientation,com_offset_body);
    result.position=rm_add(anchor,offset);
    omega=rm_world_omega(result.orientation,result.angular_momentum,result.inertia_body_diag);
    result.velocity=rm_cross(omega,offset);
    result.inv_mass=0.0;
    if(!rm_vec_finite(result.angular_momentum) || !rm_vec_finite(result.position)
       || !rm_vec_finite(result.velocity)) return 0;
    *body=result; return 1;
}
#endif
