// 支架横向宽度
stand_width = 72;
// CD盒子重心到边缘的长度参考
reference_length = 200;
// 后靠斜面（碟盒贴靠面）与水平面的夹角
support_angle = 65; // [10:1:90]
// 后靠斜面长度：从后槽底 B 到顶部，沿碟盒贴靠面测量
backrest_length = 30;
// 盒槽的法向净宽
slot_width = 4.2;
// 从槽底沿支撑方向测量的前挡有效高度
lip_height = 5;
// 壁厚，沿壁的法向测量
thickness = 2;

/* [Hidden] */
// 角度变化时重新计算底边，而不是保持固定长度。
base_depth = reference_length * cos(support_angle);
// 在二维截面中，x 为前后方向，第二坐标为使用时的高度。
u = [cos(support_angle), sin(support_angle)];
n = [-sin(support_angle), cos(support_angle)];

// 后槽底 B 位于 thickness。
// B 的 x 坐标使前挡外侧的延长线恰好经过前端 [0,0]。
B = [
    (slot_width + thickness + thickness * cos(support_angle))
        / sin(support_angle),
    thickness
];
A = B + slot_width * n;                         // 前槽底
lip_inner_top = A + lip_height * u;
lip_outer_top = lip_inner_top + thickness * n;
rear_toe = [base_depth, 0];

// 后靠斜面使用独立长度；后侧支撑边随顶点位置自动变化。
support_top = B + backrest_length * u;

module main() {
    linear_extrude(height = stand_width, convexity = 4)
        polygon([
            [0, 0],
            rear_toe,
            support_top,
            B,
            A,
            lip_inner_top,
            lip_outer_top
        ]);
}

main();
