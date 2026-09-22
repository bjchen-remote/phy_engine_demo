#include "../physics_demo/core/native/rigid_math.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures=0;
static void check(int condition,const char *name) {
    if(!condition) { fprintf(stderr,"FAIL: %s\n",name); ++failures; }
}
static double q_distance(rm_quat a,rm_quat b) {
    double minus=hypot(hypot(a.w-b.w,a.x-b.x),hypot(a.y-b.y,a.z-b.z));
    double plus=hypot(hypot(a.w+b.w,a.x+b.x),hypot(a.y+b.y,a.z+b.z));
    return fmin(minus,plus);
}
static rm_body body_default(void) {
    rm_body body;
    memset(&body,0,sizeof(body));
    body.orientation=rm_q_identity();
    body.inv_mass=1.0;
    body.inertia_body_diag=rm_v3(1.0,1.0,1.0);
    return body;
}
static void quaternion_math(void) {
    rm_vec3 v=rm_v3(1.2,-0.3,2.1);
    rm_quat q=rm_q_from_axis_angle(rm_v3(1.0,2.0,-1.0),1.3);
    rm_vec3 back=rm_q_inverse_rotate(q,rm_q_rotate(q,v));
    check(rm_norm(rm_sub(v,back))<2.0e-15,"quaternion inverse rotation");
    check(q_distance(rm_q_mul(q,rm_q_conjugate(q)),rm_q_identity())<2.0e-15,
          "quaternion multiplication convention");
    q=rm_q_from_axis_angle(rm_v3(0.0,0.0,1.0),acos(-1.0)*0.5);
    check(rm_norm(rm_sub(rm_q_rotate(q,rm_v3(1.0,0.0,0.0)),rm_v3(0.0,1.0,0.0)))
          <3.0e-16,"body-to-world right-hand convention");
    puts("PASS quaternion_math");
}
static void free_sphere(void) {
    const rm_vec3 inertia={2.0,2.0,2.0}, momentum={0.2,-0.4,1.8};
    rm_quat q=rm_q_from_axis_angle(rm_v3(0.3,1.0,0.7),0.5), initial=q;
    int step;
    for(step=0;step<5000;++step)
        check(rm_step_orientation(&q,momentum,inertia,0.001),"free sphere step");
    {
        rm_quat expected=rm_q_mul(rm_q_from_rotation_vector(rm_scale(momentum,2.5)),initial);
        check(q_distance(q,expected)<2.0e-12,"free sphere analytic attitude");
        check(fabs(rm_q_norm(q)-1.0)<3.0e-16,"free sphere unit quaternion");
    }
    puts("PASS free_sphere");
}
static void symmetric_top(void) {
    const rm_vec3 inertia={2.0,2.0,1.0}, body_m={0.3,0.4,3.0};
    rm_quat q=rm_q_from_axis_angle(rm_v3(1.0,0.2,0.0),0.4), initial=q;
    rm_vec3 momentum=rm_q_rotate(q,body_m);
    double initial_energy=rm_rotational_energy(q,momentum,inertia);
    int step;
    for(step=0;step<1000;++step)
        check(rm_step_orientation(&q,momentum,inertia,0.005),"symmetric top step");
    {
        rm_quat precession=rm_q_from_rotation_vector(rm_scale(momentum,5.0/inertia.x));
        rm_quat spin=rm_q_from_axis_angle(rm_v3(0.0,0.0,1.0),
                                        5.0*body_m.z*(1.0/inertia.z-1.0/inertia.x));
        rm_quat expected=rm_q_mul(rm_q_mul(precession,initial),spin);
        double error=q_distance(q,expected);
        check(error<1.5e-4,"free symmetric top analytic precession");
        check(fabs(rm_rotational_energy(q,momentum,inertia)-initial_energy)<2.0e-11,
              "free symmetric top energy invariant");
        printf("PASS symmetric_top attitude_error=%.9g\n",error);
    }
}
static rm_quat asymmetric_trajectory(double dt) {
    const rm_vec3 inertia={0.7,1.3,2.1}, momentum={0.8,-0.6,1.1};
    rm_quat q=rm_q_from_axis_angle(rm_v3(1.0,2.0,0.5),0.4);
    int steps=(int)lround(4.0/dt), step;
    for(step=0;step<steps;++step)
        check(rm_step_orientation(&q,momentum,inertia,dt),"asymmetric refinement step");
    return q;
}
static void asymmetric_top(void) {
    const rm_vec3 inertia={0.7,1.3,2.1};
    rm_vec3 momentum={0.8,-0.6,1.1}, original_momentum=momentum;
    rm_quat q=rm_q_from_axis_angle(rm_v3(1.0,2.0,0.5),0.4), original=q;
    double energy=rm_rotational_energy(q,momentum,inertia), max_drift=0.0;
    int step;
    for(step=0;step<10000;++step) {
        double drift;
        check(rm_step_rotation(&q,&momentum,inertia,rm_v3(0.0,0.0,0.0),0.01),
              "asymmetric free top step");
        drift=fabs(rm_rotational_energy(q,momentum,inertia)-energy);
        max_drift=fmax(max_drift,drift);
        check(fabs(rm_q_norm(q)-1.0)<5.0e-16,"asymmetric top unit quaternion");
    }
    check(rm_norm(rm_sub(momentum,original_momentum))==0.0,"zero torque world L exact invariant");
    check(max_drift<5.0e-10,"asymmetric top long-time energy invariant");
    for(step=0;step<10000;++step)
        check(rm_step_orientation(&q,momentum,inertia,-0.01),"asymmetric inverse step");
    check(q_distance(q,original)<1.0e-9,"asymmetric time reversal");
    {
        rm_quat reference=asymmetric_trajectory(0.0005);
        double coarse=q_distance(asymmetric_trajectory(0.04),reference);
        double fine=q_distance(asymmetric_trajectory(0.02),reference);
        check(coarse>3.8*fine && coarse<4.2*fine,"asymmetric second-order convergence");
        printf("PASS asymmetric_top max_energy_drift=%.9g refinement_ratio=%.9g\n",
               max_drift,coarse/fine);
    }
}
static void constant_torque(void) {
    const rm_vec3 inertia={2.0,2.0,2.0}, torque={0.0,0.0,0.7};
    rm_vec3 momentum={0.0,0.0,1.0};
    rm_quat q=rm_q_identity();
    int step;
    for(step=0;step<200;++step)
        check(rm_step_rotation(&q,&momentum,inertia,torque,0.01),"constant torque step");
    check(rm_norm(rm_sub(momentum,rm_v3(0.0,0.0,2.4)))<2.0e-14,
          "constant torque angular impulse");
    check(q_distance(q,rm_q_from_axis_angle(rm_v3(0.0,0.0,1.0),1.7))<2.0e-14,
          "constant torque spherical analytic attitude");
    puts("PASS constant_torque");
}
static double pivot_energy(rm_body body,double mass,rm_vec3 gravity) {
    return rm_rotational_energy(body.orientation,body.angular_momentum,body.inertia_body_diag)
           -mass*rm_dot(gravity,body.position);
}
static void gravity_precession(void) {
    const double mass=1.0,length=0.3,theta=0.35,spin=100.0,dt=0.0005;
    const rm_vec3 gravity={0.0,0.0,-9.81}, anchor={0.0,0.0,0.0};
    const rm_vec3 offset={0.0,0.0,0.3};
    rm_body body=body_default();
    rm_vec3 n;
    double l3,precession,initial_energy,max_drift=0.0;
    int step;
    body.inertia_body_diag=rm_v3(0.25,0.25,0.05);
    body.orientation=rm_q_from_axis_angle(rm_v3(0.0,1.0,0.0),theta);
    n=rm_q_rotate(body.orientation,rm_v3(0.0,0.0,1.0));
    l3=body.inertia_body_diag.z*spin;
    precession=2.0*mass*9.81*length/(l3+sqrt(l3*l3-4.0*body.inertia_body_diag.x*
                      cos(theta)*mass*9.81*length));
    body.angular_momentum=rm_add(rm_scale(n,l3-body.inertia_body_diag.x*precession*cos(theta)),
                                rm_v3(0.0,0.0,body.inertia_body_diag.x*precession));
    body.position=rm_pivot_com(body.orientation,anchor,offset);
    initial_energy=pivot_energy(body,mass,gravity);
    for(step=0;step<4000;++step) {
        check(rm_step_pivot(&body,anchor,offset,gravity,mass,dt),"heavy top gravity step");
        max_drift=fmax(max_drift,fabs(pivot_energy(body,mass,gravity)-initial_energy));
        check(fabs(rm_norm(rm_sub(body.position,anchor))-length)<2.0e-16,
              "fixed pivot COM distance");
    }
    n=rm_q_rotate(body.orientation,rm_v3(0.0,0.0,1.0));
    {
        double azimuth=atan2(n.y,n.x), expected=2.0*precession;
        check(azimuth>0.0,"gravity precession sign");
        check(fabs(azimuth-expected)<0.002,"heavy top analytic regular precession");
        check(fabs(n.z-cos(theta))<2.0e-5,"heavy top steady inclination");
        check(max_drift<2.0e-5,"heavy top bounded total energy drift");
        printf("PASS gravity_precession expected=%.9g actual=%.9g energy_drift=%.9g\n",
               expected,azimuth,max_drift);
    }
}
static void impulse_response(void) {
    rm_body a=body_default(), b=body_default();
    const rm_vec3 r={1.0,0.0,0.0}, normal={0.0,1.0,0.0}, impulse={0.0,3.0,0.0};
    rm_vec3 initial_velocity,initial_momentum;
    a.inv_mass=0.5; a.inertia_body_diag=rm_v3(2.0,3.0,4.0);
    b.inv_mass=0.25;
    check(fabs(rm_contact_inv_mass(&a,r,normal)-0.75)<1.0e-15,
          "contact inverse mass includes moment arm");
    rm_apply_impulse(&a,r,impulse);
    rm_apply_impulse(&b,rm_v3(0.0,0.0,0.0),rm_scale(impulse,-1.0));
    check(rm_norm(rm_add(rm_scale(a.velocity,2.0),rm_scale(b.velocity,4.0)))<1.0e-15,
          "equal opposite contact linear momentum");
    check(rm_norm(rm_sub(a.angular_momentum,rm_cross(r,impulse)))<1.0e-15,
          "contact angular impulse");
    check(fabs(rm_dot(rm_point_velocity(&a,r),normal)-2.25)<1.0e-15,
          "contact normal response agrees effective mass");
    initial_velocity=a.velocity; initial_momentum=a.angular_momentum;
    check(rm_apply_position_impulse(&a,r,rm_scale(normal,1.0e-6)),"position impulse accepted");
    check(fabs(a.position.y-0.5e-6)<1.0e-20,"position impulse COM correction");
    check(q_distance(a.orientation,rm_q_from_rotation_vector(rm_v3(0.0,0.0,0.25e-6)))<1.0e-20,
          "position impulse rotational correction");
    check(rm_norm(rm_sub(a.velocity,initial_velocity))==0.0
          && rm_norm(rm_sub(a.angular_momentum,initial_momentum))==0.0,
          "position impulse leaves velocity feedback to caller");
    /* Off-centre equal/opposite impulses conserve total angular momentum
     * about a shared world origin, including translation of the COM. */
    a=body_default(); b=body_default();
    a.position=rm_v3(-1.0,0.0,0.0); b.position=rm_v3(1.0,0.0,0.0);
    rm_apply_impulse(&a,rm_v3(1.0,0.0,0.0),impulse);
    rm_apply_impulse(&b,rm_v3(-1.0,0.0,0.0),rm_scale(impulse,-1.0));
    check(rm_norm(rm_add(rm_add(a.angular_momentum,b.angular_momentum),
          rm_add(rm_cross(a.position,a.velocity),rm_cross(b.position,b.velocity))))<1.0e-15,
          "contact total angular momentum about common origin");
    puts("PASS impulse_response");
}
static void input_safety(void) {
    rm_quat q=rm_q_identity(), before=q;
    rm_vec3 momentum={1.0,2.0,3.0}, original=momentum;
    rm_body body=body_default();
    check(!rm_step_rotation(&q,&momentum,rm_v3(1.0,0.0,1.0),rm_v3(0.0,0.0,0.0),0.1),
          "reject nonpositive inertia");
    check(!rm_step_rotation(&q,&momentum,rm_v3(1.0,1.0,1.0),rm_v3(NAN,0.0,0.0),0.1),
          "reject nonfinite torque");
    check(q_distance(q,before)==0.0 && rm_norm(rm_sub(momentum,original))==0.0,
          "invalid step is transactional");
    check(!rm_step_orientation(NULL,momentum,rm_v3(1.0,1.0,1.0),0.1),"reject null orientation");
    check(!rm_apply_position_impulse(&body,rm_v3(1.0,0.0,0.0),rm_v3(INFINITY,0.0,0.0)),
          "reject nonfinite position impulse");
    check(!rm_step_pivot(&body,rm_v3(0.0,0.0,0.0),rm_v3(0.0,0.0,1.0),
          rm_v3(0.0,0.0,-9.81),-1.0,0.1),"reject nonpositive pivot mass");
    puts("PASS input_safety");
}
int main(void) {
    quaternion_math(); free_sphere(); symmetric_top(); asymmetric_top();
    constant_torque(); gravity_precession(); impulse_response(); input_safety();
    if(failures) { fprintf(stderr,"%d failed rigid-math checks\n",failures); return EXIT_FAILURE; }
    puts("all rigid-math checks passed"); return EXIT_SUCCESS;
}
